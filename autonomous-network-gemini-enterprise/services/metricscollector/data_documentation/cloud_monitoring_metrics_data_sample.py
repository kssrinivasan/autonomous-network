
# Documentation
# here is a metric descriptor sample
"""
name: "projects/networkagent-434609/metricDescriptors/prometheus.googleapis.com/frr_bfd_peer_count/gauge"
labels {
  key: "otel_scope_version"
}
labels {
  key: "router_name"
}
labels {
  key: "otel_scope_name"
}
labels {
  key: "instance_name"
}
labels {
  key: "machine_type"
}
metric_kind: GAUGE
value_type: DOUBLE
description: "Number of peers detected."
type: "prometheus.googleapis.com/frr_bfd_peer_count/gauge"
monitored_resource_types: "prometheus_target
"""

# here is a matric time series sample:
"""
metric {
  labels {
    key: "router_name"
    value: "p1"
  }
  labels {
    key: "otel_scope_version"
    value: "v0.138.0"
  }
  labels {
    key: "otel_scope_name"
    value: "github.com/open-telemetry/opentelemetry-collector-contrib/receiver/prometheusreceiver"
  }
  labels {
    key: "machine_type"
    value: "projects/784219571708/machineTypes/e2-standard-16"
  }
  labels {
    key: "instance_name"
    value: "networkvm"
  }
  type: "prometheus.googleapis.com/frr_bfd_peer_count/gauge"
}
resource {
  type: "prometheus_target"
  labels {
    key: "project_id"
    value: "networkagent-434609"
  }
  labels {
    key: "namespace"
    value: "3515116987487830921/networkvm"
  }
  labels {
    key: "location"
    value: "europe-west1-c"
  }
  labels {
    key: "job"
    value: "vyos-lab"
  }
  labels {
    key: "instance"
    value: "192.168.122.11:9101"
  }
  labels {
    key: "cluster"
    value: "__gce__"
  }
}
metric_kind: GAUGE
value_type: DOUBLE
points {
  interval {
    start_time {
      seconds: 1771312114
      nanos: 864000000
    }
    end_time {
      seconds: 1771312114
      nanos: 864000000
    }
  }
  value {
    double_value: 0
  }
}
points {
  interval {
    start_time {
      seconds: 1771312099
      nanos: 864000000
    }
    end_time {
      seconds: 1771312099
      nanos: 864000000
    }
  }
  value {
    double_value: 0
  }
}
points {
  interval {
    start_time {
      seconds: 1771312084
      nanos: 864000000
    }
    end_time {
      seconds: 1771312084
      nanos: 864000000
    }
  }
  value {
    double_value: 0
  }
}
points {
  interval {
    start_time {
      seconds: 1771312069
      nanos: 864000000
    }
    end_time {
      seconds: 1771312069
      nanos: 864000000
    }
  }
  value {
    double_value: 0
  }
}

"""