# Network Controller

This kubernetes controller (`controller/`, formerly `operator/`) manages the lifecycle of network functions and network services used in the demo, along with automation of some of the demo infrastructure itself.

The operator code is based on the [kopf](https://kopf.readthedocs.io/en/latest/) operator framework and embeds [ansible playbooks](https://docs.ansible.com/) to run commands inside the network VM.

## Build and deploy the operator

The operator is automatically deployed when `./install.sh --platform` (or `./install.sh --all`) is run. To update to a new version of the operator run the following command from the repository root.

```
cd controller && ./install.sh
```

To see operator logs run the following commands. 

```
kubectl get pods
NAME                          READY   STATUS      RESTARTS   AGE
networkoperator-<UUID>        1/1     Running     0          88s
kubectl logs -f networkoperator-<UUID>
```
