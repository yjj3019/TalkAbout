# Podman verification and operating approval checklist

**SYNTHETIC VERIFICATION ONLY / OPERATING DEPLOYMENT NOTRUN — 2026-10-13**

| Check | Result / evidence |
|---|---|
| Python aggregate | 100 PASS at f6c162b01986ed4ce6d0ed2bb40e5b6abedfbbbc, including 11 isolated real PostgreSQL cases and local stdio MCP |
| UI logic | 5 PASS; actual browser/SSE NOTRUN |
| Later packaging corrections | Targeted packaging 5 PASS; no production Python/Containerfile/runtime dependency changes after the aggregate |
| Exact-source rootless build/smoke | PASS at b35c7ce91a99e8d3bb79579abf39fa7246a24e5d |
| Built image ID | sha256:f1524d2448111506f80268db299c6249fff7450cf917340b655dfb62e9caa938 |
| Image identity | User 10001:10001, linux/amd64, HEALTHCHECK retained in Docker format |
| App and DB readiness | PASS, private fixture with no published ports |
| DB outage | App healthcheck fails while DB unavailable; manual fixture app restart after DB recovery restores readiness |
| Persistence | Synthetic marker survives DB/container restart with the same owned named volume |
| Logical restore | Custom-format dump restored to another database inside the isolated fixture; marker verified |
| Fixture cleanup | PASS: randomized labelled containers, secrets, volume and network removed |
| CI | Not configured; local/remote explicit verification is separate evidence |
| Operating deployment | NOTRUN; no real human/agent credential, public port or auto-start |

The first OCI build dropped image HEALTHCHECK; the Docker-format build and explicit native HealthCmd correct this. A launch incompatibility rejected tmpfs uid/gid options; the private socket directory now uses supported mode options. A read-only root rejected secret mountpoint creation; only a 1 MiB root-owned /run/secrets tmpfs was added. Cap-drop/no-new-privileges/non-root/resource/private-network safeguards were retained. An initially retained fixture network was removed only after exact ownership/emptiness verification; subsequent fixture cleanup completed normally.

No secret values, target aliases, addresses, user paths or operating data are recorded here. Base version/digest metadata is in [images.lock.json](images.lock.json). Build artifacts remain available for the approved project; no unrelated resources were pruned.

## Proposed operating resources — approval pending

The following concrete proposal must be approved before persistent operating resources or credentials are created:

| Resource | Proposal / purpose |
|---|---|
| Containers | talkabout-app and talkabout-db; rootless, app UID 10001 / DB UID 999, cap-drop all, no-new-privileges, read-only root |
| Network | talkabout-private, internal only; no host network or runtime socket mounts |
| Persistence | talkabout-pg18-data mounted only at /var/lib/postgresql; protected backup location and retention chosen by owner |
| Port | Host 127.0.0.1:18080 → app 8080 proposed; recheck availability before launch, do not change an existing listener; DB has no published port |
| Secret refs | talkabout-db-password for DB owner initialization; talkabout-db-init-url for one-time schema owner; talkabout-db-url for least-privilege runtime role |
| DB roles | Dedicated talkabout_owner and talkabout_app; operating passwords supplied by the user/approved secret path, no values in repository or command args |
| Human bootstrap | One explicit interactive bootstrap-human action, user chooses workspace/name/handle/password; agent tokens not automatically issued |
| Startup | Manual only; no Quadlet enable, linger or automatic boot/restart yet |
| Limits | Each app/DB: 1 CPU, 512 MiB memory, 128 PIDs; bounded tmpfs/logs; deployment fails instead of weakening unsupported limits |
| External access | No public bind/firewall/TLS change. Any later access/publication policy requires separate approval |

Operating MFA/recovery, production backup/restore/cutover, retention/archival/disk policies and real-runtime PoC remain separate requirements. This checklist is a reviewable proposal, not authorization to apply it.
