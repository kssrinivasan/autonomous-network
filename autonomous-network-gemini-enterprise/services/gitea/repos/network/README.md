# Network Automation Repository

This repository contains network automation configurations managed via Kubernetes Config Sync.

## Branches

- `main`: Stable, approved configurations
- `update`: Pending changes awaiting review and approval

## Workflow

1. Automated agents push proposed changes to `update` branch
2. Webhook triggers trust/approval workflow
3. Approved changes are merged to `main`
4. Config Sync reconciles cluster state from `main`
