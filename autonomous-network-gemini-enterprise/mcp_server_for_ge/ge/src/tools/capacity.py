# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import logging
from typing import Annotated
from mcp.types import ToolAnnotations
from utils.globals import networkagent_mcp
from utils.database import get_database
from tools.topology import CAPACITY_TIERS, query_link_utilization

logger = logging.getLogger(__name__)


def _link_utilizations(time_window_minutes):
    """Return measured utilization for every current PhysicalLink.

    Delegates to query_link_utilization rather than recomputing, because that
    is the one implementation that derives throughput from the
    RouterSystemMetrics byte counters on both endpoints of a link. This module
    used to multiply capacity by a hardcoded 0.78 instead, so every link
    reported exactly 78% no matter what the network was doing -- and
    recommend_capacity_upgrades then "recommended" an upgrade for a link name
    it had made up.

    Raises RuntimeError if the utilization query itself failed, so callers can
    tell that apart from "nothing is congested".
    """
    with get_database().snapshot(multi_use=True) as snapshot:
        rows = snapshot.execute_sql(
            "SELECT name, status FROM PhysicalLink "
            "WHERE valid_end_ts IS NULL AND name IS NOT NULL ORDER BY name"
        )
        statuses = {row[0]: row[1] for row in rows}

    if not statuses:
        return []

    result = json.loads(query_link_utilization(",".join(statuses.keys()), time_window_minutes))
    if isinstance(result, dict) and result.get("error"):
        raise RuntimeError(result["error"])

    links = result.get("links", [])
    for link in links:
        link["status"] = statuses.get(link.get("name")) or "UNKNOWN"
    return links


@networkagent_mcp.tool()
def identify_bottlenecks(
    utilization_threshold_pct: Annotated[float, "Utilization threshold percentage (default 50.0)"] = 50.0,
    time_window_minutes: Annotated[int, "Time window in minutes for metrics (default 10)"] = 10
) -> str:
    """Identify physical network links whose measured peak throughput exceeds a utilization threshold."""
    logger.info(f"identify_bottlenecks: {utilization_threshold_pct}% over {time_window_minutes}m")
    try:
        links = _link_utilizations(time_window_minutes)
    except Exception as e:
        logger.error(f"Error in identify_bottlenecks: {e}")
        return json.dumps({"error": str(e)})

    if not links:
        return (
            "No physical links with utilization data were found in the transport "
            "topology. Either no network is deployed or the metrics collector has "
            "not written interface counters for these routers yet."
        )

    bottlenecks = [
        {
            "name": link["name"],
            "status": link["status"],
            "router_a": link.get("router_a"),
            "router_b": link.get("router_b"),
            "capacity_bps": link.get("capacity_bps"),
            "current_traffic_bps": link.get("current_utilization_bps"),
            "available_bps": link.get("available_bps"),
            "utilization_pct": link.get("utilization_pct"),
            "time_window_minutes": time_window_minutes,
        }
        for link in links
        if (link.get("utilization_pct") or 0) >= utilization_threshold_pct
    ]

    if not bottlenecks:
        busiest = max(links, key=lambda l: l.get("utilization_pct") or 0)
        return (
            f"No link reached {utilization_threshold_pct}% utilization over the last "
            f"{time_window_minutes} minutes. The busiest of the {len(links)} links is "
            f"'{busiest['name']}' at {busiest.get('utilization_pct', 0)}%."
        )

    bottlenecks.sort(key=lambda l: l["utilization_pct"], reverse=True)
    return json.dumps(bottlenecks, indent=2)


@networkagent_mcp.tool()
def recommend_capacity_upgrades(
    target_utilization_pct: Annotated[float, "Target link utilization percentage (default 40.0)"] = 40.0,
    time_window_minutes: Annotated[int, "Time window in minutes for metrics (default 10)"] = 10
) -> str:
    """Recommend physical link bandwidth upgrades for links whose measured peak traffic exceeds a target utilization."""
    logger.info(f"recommend_capacity_upgrades: target {target_utilization_pct}% over {time_window_minutes}m")
    if target_utilization_pct <= 0:
        return json.dumps({"error": "target_utilization_pct must be greater than 0"})

    try:
        links = _link_utilizations(time_window_minutes)
    except Exception as e:
        logger.error(f"Error in recommend_capacity_upgrades: {e}")
        return json.dumps({"error": str(e)})

    if not links:
        return (
            "No physical links with utilization data were found, so there is "
            "nothing to size. Deploy the transport topology and let the metrics "
            "collector run before asking for capacity recommendations."
        )

    recommendations = []
    for link in sorted(links, key=lambda l: l.get("utilization_pct") or 0, reverse=True):
        util_pct = link.get("utilization_pct") or 0
        if util_pct < target_utilization_pct:
            continue

        current_bps = link.get("current_utilization_bps") or 0
        capacity_bps = link.get("capacity_bps") or 0
        # The capacity at which the traffic actually measured on this link would
        # sit at the target utilization.
        required_bps = current_bps / (target_utilization_pct / 100.0)

        tier = next(
            ((bps, label) for bps, label in CAPACITY_TIERS if bps >= required_bps and bps > capacity_bps),
            None,
        )
        if tier is None:
            recommendations.append({
                "link": link["name"],
                "router_a": link.get("router_a"),
                "router_b": link.get("router_b"),
                "current_bandwidth_bps": capacity_bps,
                "measured_utilization_pct": util_pct,
                "recommended_bandwidth": None,
                "rationale": (
                    f"Peak traffic of {current_bps:,} bps needs roughly "
                    f"{int(required_bps):,} bps to sit at {target_utilization_pct}%, "
                    f"which is beyond the largest standard link speed "
                    f"({CAPACITY_TIERS[-1][1]}). Relieve this link by traffic engineering or "
                    "by adding a parallel link rather than by upgrading it."
                ),
            })
            continue

        tier_bps, tier_label = tier
        recommendations.append({
            "link": link["name"],
            "router_a": link.get("router_a"),
            "router_b": link.get("router_b"),
            "current_bandwidth_bps": capacity_bps,
            "measured_utilization_pct": util_pct,
            "recommended_bandwidth": tier_label,
            "recommended_bandwidth_bps": tier_bps,
            "projected_utilization_pct": round(current_bps / tier_bps * 100, 2),
            "rationale": (
                f"Measured peak of {current_bps:,} bps over the last "
                f"{time_window_minutes} minutes is {util_pct}% of the link's "
                f"{capacity_bps:,} bps. Moving to {tier_label} puts the same peak at "
                f"{round(current_bps / tier_bps * 100, 2)}%, at or below the "
                f"{target_utilization_pct}% target."
            ),
        })

    if not recommendations:
        return (
            f"No link exceeds the {target_utilization_pct}% target utilization over "
            f"the last {time_window_minutes} minutes, so no capacity upgrade is "
            f"warranted across the {len(links)} links measured."
        )
    return json.dumps(recommendations, indent=2)
