# Examples

Sample manifests and intent payloads for exercising the autonomous network lab.

## Directory Summary

| Directory | Description |
|-----------|-------------|
| `controller/` | Kubernetes CR manifests applied directly to the controller |
| `controller/transport/` | Transport-only network examples (VyOS infrastructure, underlay, L3VPN) in three sizes: `default-network`, `medium-network`, `large-network` |
| `controller/multidomain/` | Full multi-domain network examples (transport + core + radio + service) in four sizes: `tiny`, `default`, `medium`, `large` |
| `intents/` | TMF921 intent YAML payloads sent to the intent server (`transport-default.yaml`, `core-default.yaml`, `radio-default.yaml`, `service-default.yaml`) |
| `failure_tests/` | Network failure injection manifests for fault simulation and RCA testing, organised by domain (`transport/`, `core/`, `radio/`) and network size |

## Usage

### Apply a multi-domain network

```bash
kubectl apply -f examples/controller/multidomain/default/
```

### Apply a transport-only network

```bash
kubectl apply -f examples/controller/transport/default-network/
```

### Inject a network failure

```bash
kubectl apply -f examples/failure_tests/transport/default/link-down-bridge.yaml
```

### Send an intent to the intent server

```bash
curl -X POST <INTENT_SERVER_URL>/v1/intents \
  -H "Content-Type: application/json" \
  -d @examples/intents/transport-default.yaml
```
