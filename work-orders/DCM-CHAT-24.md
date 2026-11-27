# DCM-CHAT-24 — Chat permission groups: live intersection, remove lifecycle machinery (part of HRAM-CHT-12)

Pointer stub. The authoritative Envelope, Part B and Gate record live in `hram/work-orders/HRAM-CHT-12.md`.

This repository's part:
- reduce the published 3.44.0 group grants to the live rule: each private chat stores its enabled group IDs (nothing = all read groups); every tool offer, invocation and data selector intersects them with the members the app currently declares available;
- remove capability generations, catalogue activation, the frozen baseline and cohort, the authorization snapshot hook, reauthorization and the feature's provider-continuation, upload and Claude-CLI revocation, with their tests and tables;
- keep the read and save API compatible with ui-core-micha 4.41.0; release as 3.45.0.

Reviewed per its own WO ID.
