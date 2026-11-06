# Privacy services and application rollout

Status: all 24 privacy specifications and the shared privacy prototype approved for implementation on 2026-11-06. Specifications authored 2026-11-04. BCO-LEGAL-5 and its legal-reader prototype, plus UCM-PRIV-3 and its public-footer comparison, were additionally approved on 2026-11-06 and are coordinated below: 26 work orders in total, with no duplicate app adoption orders.

## Decisions and scope

The operator requested work orders for shared dcm/ucm services and **all apps**, based on Sol's read-only source inventory. Explicit decisions: prior opt-in for optional services uniformly across CH/EU; visit measurement only after choice; one common visual prototype. The scope is the 20 repositories in webapp-management/recovery/staging/adoption-inventory.json (20 apps). All checkouts were present. The standalone DHIS2 repository is explicitly excluded by the operator. The staging catalogue is a scope baseline, not proof that all current production deployments or edge-injected services were observed.

Gate #1 approved by the operator 2026-11-06 (Codex Expertenchat, session 01a1a807-3193-7793-8e43-50d6d7fd7263), after accepting the interactive privacy prototype and explicitly requesting release of the work orders and printed implementation instructions. This approval covers all 24 Envelopes and the shared prototype/copy. Dependencies, actual-provider verification and activation prerequisites remain binding. Production promotion remains separately gated. No implementation was started by the requirements session.

## Dependency order

1. DCM-PRIV-1 supplies public-safe manifest, notice references, anonymous/browser-scoped consent receipts and enforcement.
2. UCM-PRIV-1 supplies common UI and gated lifecycle against that contract.
3. BCO-LEGAL-5 supplies the branded, app-aware legal reader and publication-registry interface in parallel with shared consent work. BCO-PRIV-1 reuses it for processing disclosures and adoption. Reader/registry changes land before overlapping BCO-PRIV-1 work and before consumer links activate in that environment.
4. Each app adoption below is independently reviewable and retains its controller and functional data. No release is automatically rolled across apps. Integrate the footer entries during that app's existing PRIV-1 implementation, using the coordinated handoff below.
5. UCM-PRIV-2 removes old UI/adapters after caller migration; DCM-PRIV-2 removes the old profile fields with concrete consumer migrations after UI adoption. Neither removal is complete without its app follow-through.

The static report follows the same privacy principles without acquiring a Django backend or a fake banner. No app is required to add an optional purpose that it does not currently need.

## Coordinated legal reader and footer handoff — approved 2026-11-06

The operator accepted the revised legal-reader prototype, requested release of [BCO-LEGAL-5](../../bigler-consult/work-orders/BCO-LEGAL-5.md), and requested coordination with the existing privacy orders so each app receives footer entry points. The operator also confirmed that the included applications are own apps. BCO-LEGAL-5's visual reference is `bigler-consult/work-orders/assets/app-prototype.html@319cdd1`, `page=legal`, with the committed 375/1280 comparison. The shared privacy prototype remains `ui-core-micha@ced7a28`; the two prototypes govern different surfaces.

This section is the common orchestration handoff for the existing app Envelopes' document and persistent-settings integration, not a separate implementer dispatch. Copy the relevant app-specific requirements into Part B before dispatch. Preserve each Envelope's functional boundaries, processing disclosures, independent permissions and tests; do not use this coordination to bypass an unresolved prerequisite.

- Apply the approved [UCM-PRIV-3 footer supplement](../../ui-core-micha/work-orders/UCM-PRIV-3.md): full footers only on public pages, including login/registration; authenticated workspaces expose legal links and local privacy settings through the account/app menu instead. A signed-in visitor on a public page still sees its public footer. This supersedes the earlier instruction to put full footers in authenticated shells. The exact approved Maana/Imara/Spesix comparison is `ui-core-micha/work-orders/assets/public-footer-comparison.html@18e95f5`, with 1280/375 frames for each app. Carry this placement and visual reference into each existing app adoption's Part B, preserving its identity.
- Put persistent, translated Privacy notice and Terms links in those public footers and authenticated menus, plus a local Privacy settings action in the 19 interactive apps. Public links require neither login nor optional consent. Avoid duplicate footers. Preserve existing imprint and applicable cancellation/DPA links. Other legal documents remain reachable in the central reader; do not imply that Maana-specific commercial clauses apply to unrelated apps.
- Resolve document destinations from the app's validated legal/notice configuration and the one central publication registry. The link contract is `/legal/<existing-document-slug>?app=<registered-id>&lang=<document-locale>` on the configured bigler-consult origin; for example `/legal/privacy?app=maana&lang=de`. Use stable registered IDs, not display names or Referer inference. Production links target the published production reader; staging verification targets the matching staging reader. Do not embed account identifiers, source-page URLs or secrets.
- Known own apps without a special edition show the complete general text directly, with selected app and discreet General version metadata. Published app-specific editions take precedence. Keep the selected app while changing document or language. Historical acceptance references remain pinned to their actual version; footer links may resolve the current published version. UI language and document language remain distinct: use existing verified DE/EN documents and an explicit supported fallback for other UI locales; never manufacture a translated legal text.
- Privacy settings open the originating app's shared UCM settings, not bigler-consult's consent controls. Choices remain site/browser scoped. Necessary-only interactive apps keep the informative settings entry but show no first-choice banner. A failed settings API must not hide ordinary legal hyperlinks. The static YOP report gets legal links through its approved upstream/host authoring path, without a fake settings action, new backend or consent banner; its verbatim mirror prerequisite remains binding.
- Keep collection-specific notices where needed. Central footer links do not replace tenant/study-authored research notices, requested AI disclosures or actual terms acceptance. Common text inheritance is not evidence that the app's actual processing inventory is complete.
- Per app, verify the rendered public footer and authenticated account/app-menu entries where present, destination content, keyboard/mobile reachability, selected app and document locale, general-version inheritance where applicable, and local settings reopening after refusal. Confirm authenticated workspaces do not retain a duplicate full footer. Use the existing scoped harness and record target environment/version. Include one actual cross-app navigation check with Maana and one with an app inheriting the general text; do not add a browser harness or duplicate the full privacy test matrix for every link.

Completion requires all 20 app orders to have evidence for their applicable entry points; name and track unresolved prerequisites as incomplete work. A shared-component release alone is not estate-wide adoption. Source work may proceed independently, but consumer production activation waits for the required central target routes and verified document mappings to be live. Production promotion retains its separate gate.

## Work orders

| Repository | Work order | Outcome | Publication |
|---|---|---|---|
| django-core-micha | [DCM-PRIV-1](../../django-core-micha/work-orders/DCM-PRIV-1.md) | Shared privacy information and purpose-specific consent | Prepared; publication recorded below |
| ui-core-micha | [UCM-PRIV-1](../../ui-core-micha/work-orders/UCM-PRIV-1.md) | Shared privacy choices and notice surfaces | Prepared; publication recorded below |
| ui-core-micha | [UCM-PRIV-3](../../ui-core-micha/work-orders/UCM-PRIV-3.md) | Approved public footer comparison and authenticated menu placement across the existing 20 app adoptions | Gate #1 approved 2026-11-06 in session 01a1a885-1278-78f3-8cc2-fd110de60445; prototype pinned at 18e95f5 |
| bigler-consult | [BCO-LEGAL-5](../../bigler-consult/work-orders/BCO-LEGAL-5.md) | Branded app-aware legal reader and shared footer destination contract | Gate #1 approved 2026-11-06; approval recorded in its WO and register |
| hram | [HRAM-PRIV-1](../../hram/work-orders/HRAM-PRIV-1.md) | Separate map preferences from requested mapping | Prepared; publication recorded below |
| Gustav | [GUS-PRIV-1](../../Gustav/work-orders/GUS-PRIV-1.md) | Adopt privacy controls without losing local customer work | Prepared; publication recorded below |
| Photogallery | [PG-PRIV-1](../../Photogallery/work-orders/PG-PRIV-1.md) | Separate gallery analytics from anonymous reactions | Prepared; publication recorded below |
| Cinevia | [CIN-PRIV-1](../../Cinevia/work-orders/CIN-PRIV-1.md) | Adopt privacy controls across production and live shells | Prepared; publication recorded below |
| maana | [MAANA-PRIV-1](../../maana/work-orders/MAANA-PRIV-1.md) | Adopt privacy controls and correct legal document integration | Prepared; publication recorded below |
| Kira | [KIRA-PRIV-1](../../Kira/work-orders/KIRA-PRIV-1.md) | Make story identity and maps action-bound | Prepared; publication recorded below |
| webshop-guenter | [WSG-PRIV-1](../../webshop-guenter/work-orders/WSG-PRIV-1.md) | Adopt customer privacy choices without gating the cart | Prepared; publication recorded below |
| reimbursements | [RMB-PRIV-1](../../reimbursements/work-orders/RMB-PRIV-1.md) | Replace inherited cookie flags in reimbursement flows | Prepared; publication recorded below |
| jg-ferien | [JG-PRIV-1](../../jg-ferien/work-orders/JG-PRIV-1.md) | Classify client identity and gate optional event services | Prepared; publication recorded below |
| hpc-bridge | [HPC-PRIV-1](../../hpc-bridge/work-orders/HPC-PRIV-1.md) | Adopt browser privacy controls in the bridge console | Prepared; publication recorded below |
| fitness-monitor | [FM-PRIV-1](../../fitness-monitor/work-orders/FM-PRIV-1.md) | Replace inherited privacy acceptance in fitness monitoring | Prepared; publication recorded below |
| survey_contact_app | [SCA-PRIV-1](../../survey_contact_app/work-orders/SCA-PRIV-1.md) | Adopt privacy information without coupling research contacts | Prepared; publication recorded below |
| kerzenziehen | [KZ-PRIV-1](../../kerzenziehen/work-orders/KZ-PRIV-1.md) | Preserve queue credentials while adopting privacy controls | Prepared; publication recorded below |
| bigler-consult | [BCO-PRIV-1](../../bigler-consult/work-orders/BCO-PRIV-1.md) | Publish accurate app-specific privacy information and adopt controls | Prepared; publication recorded below |
| spesix | [SPX-PRIV-1](../../spesix/work-orders/SPX-PRIV-1.md) | Adopt privacy choices without coupling financial and OCR permissions | Prepared; publication recorded below |
| survey_app | [SVA-PRIV-1](../../survey_app/work-orders/SVA-PRIV-1.md) | Separate browser choices from study consent across survey sites | Prepared; publication recorded below |
| cockpit | [CKP-PRIV-1](../../cockpit/work-orders/CKP-PRIV-1.md) | Adopt browser privacy controls without relaxing ingest boundaries | Prepared; publication recorded below |
| innoservice | [INNO-PRIV-1](../../innoservice/work-orders/INNO-PRIV-1.md) | Adopt privacy choices while preserving measurement drafts | Prepared; publication recorded below |
| yopoulab-hhs-2026 | [YOP-PRIV-1](../../yopoulab-hhs-2026/work-orders/YOP-PRIV-1.md) | Make the mirrored report self-contained and disclose its processing | Prepared; publication recorded below |
| musiknoten | [MUS-PRIV-1](../../musiknoten/work-orders/MUS-PRIV-1.md) | Adopt privacy information while preserving no-storage guarantees | Prepared; publication recorded below |
| ui-core-micha | [UCM-PRIV-2](../../ui-core-micha/work-orders/UCM-PRIV-2.md) | Retire legacy privacy UI after adoption | Prepared; publication recorded below |
| django-core-micha | [DCM-PRIV-2](../../django-core-micha/work-orders/DCM-PRIV-2.md) | Retire legacy privacy profile fields after adoption | Prepared; publication recorded below |

## Prototype

[Shared living prototype](../../ui-core-micha/work-orders/assets/app-prototype.html) at **ui-core-micha@ced7a28**; [rendered two-width comparison](../../ui-core-micha/work-orders/assets/privacy-prototype-two-widths.png). First-choice and settings surfaces at 375 and 1280 px, DE/EN and saved/error/necessary-only examples. Rendered locally with a separate headless Chrome profile; no application or provider account was changed. This establishes prototype appearance, not implemented behavior or legal compliance. All explanatory copy is listed in UCM-PRIV-1 for approval.

The single shared prototype is intentionally reused by the 19 managed interactive apps. Their own theme and locale requirements remain authoritative. Existing app living prototypes are not edited by this specification batch. Static report notice integration stays in the existing host layout.

## Evidence and activation limits

- Shared old cookie/notice controls use unversioned profile booleans. Twelve old Welcome pages write a one-year convenience_cookies cookie with no discovered reader; hram's profile value separately gates map memory.
- Photogallery couples visit measurement and reaction identity; Kira creates reaction ownership identity on a story GET. Their WOs separate purposes and delay identity creation appropriately.
- Maps/CDNs, offline work, tickets/carts and research consent are explicitly covered per app. Explicitly requested work is not blanket optional tracking.
- No generic GA/GTM/Matomo/PostHog/Sentry loader was found in the bounded first-party scan. This is not a runtime or edge audit.
- Maana's checked defaults did not match central legal routes; MAANA-PRIV-1 verifies/corrects the real current configuration, coordinated with the independent billing work.
- Actual controller/provider/retention, host-owned storage, uploaded notice texts and edge injection require evidence before app activation. A missing classification keeps optional processing off; it does not authorise inventing a provider claim or blocking unrelated necessary functionality.
- The initial Gustav checkout was stale. It was fast-forwarded during specification authoring; GUS-PRIV-1 explicitly rechecks its current local-work persistence before mapping.
- YOP-PRIV-1 preserves the verbatim upstream-mirror contract and requires an accepted upstream authoring change.

## Verification required at implementation

Per-app matrix: anonymous first visit, refusal, selective grant, reload, revoke, and consent/config failure; inspect actual browser storage and request destinations on the relevant public/authenticated routes. Add the changed functional path (gallery reactions, map, survey, ticket, cart, expense, etc.) with optional purposes denied. Use the existing browser harness where joined. Record deployment identity and distinguish local/staging/production evidence. Shared guards also need server-side negative assertions, stale-tab races and permission inventory checks; a checkbox test is not sufficient.

Do not run bulk production browsing through authenticated accounts, read secrets, dispatch deployments or make provider-console changes as a consequence of this index. Production promotion and mutating operational actions retain their normal gates.

## Legal and provider references

These are design inputs, not a complete legal opinion or evidence of any individual deployment's settings.

- [TDDDG section 25](https://www.gesetze-im-internet.de/ttdsg/__25.html): distinguish required endpoint storage/access from consent-dependent use.
- [EDPB cookie-banner taskforce](https://www.edpb.europa.eu/system/files/2023-01/edpb_20230118_report_cookie_banner_taskforce_en.pdf): free choice, refusal and absence of manipulative design.
- [EDPB legal bases](https://www.edpb.europa.eu/topics/key-gdpr-concepts/legal-basis_en): privacy information is not a universal consent-based legal basis.
- [Swiss FDPIC cookie information](https://www.edoeb.admin.ch/de/publication?id=ByXs53ExpIEy): Swiss and EU rules differ; the uniform opt-in above is the operator's product decision.
- [Cloudflare clearance](https://developers.cloudflare.com/cloudflare-challenges/concepts/clearance/): Turnstile inclusion alone does not prove a cf_clearance cookie; pre-clearance configuration must be checked separately.

## Publication record

All 24 original work orders and the shared prototype are published, verified on 2026-11-06. App specifications use `origin/develop`; shared libraries use `origin/main`. The table below records the original specification commits. The dated Gate #1 record is in each work order's Part C and register entry. No privacy feature is implemented by these specification files.

| Repository | Specification commit | Publication |
|---|---|---|
| django-core-micha | `32e3cc4` | Published; the operator explicitly authorised publishing the preceding DCM-BILL-8 specification. |
| ui-core-micha | `032b1e7`; prototype `ced7a28` | Published; verified reachable from origin/main on 2026-11-06. |
| hram | `1e6ecc32` | Published |
| Gustav | `03b6b79` | Published |
| Photogallery | `d7d8c84` | Published |
| Cinevia | `dc9b625` | Published |
| maana | `4aa05b5` | Published; the operator explicitly authorised publishing the preceding billing specifications and secrets.yaml declaration. |
| Kira | `6e26507` | Published |
| webshop-guenter | `08eee06` | Published |
| reimbursements | `3f4d3f2` | Published |
| jg-ferien | `b511e76` | Published |
| hpc-bridge | `73916ea` | Published |
| fitness-monitor | `d277f2b` | Published |
| survey_contact_app | `64d5d95` | Published |
| kerzenziehen | `2050f60` | Published |
| bigler-consult | `f9150a0` | Published |
| spesix | `fad8edd` | Published |
| survey_app | `1c1ea4c` | Published by the concurrent owner session; verified reachable from origin/develop. |
| cockpit | `9ed6fc6` | Published |
| innoservice | `1c6c16c` | Published |
| yopoulab-hhs-2026 | `c25ad53` | Published |
| musiknoten | `66e7e2a` | Published |

The operator authorised the Gustav, Spesix and Innoservice Git repairs. Gustav's obstructing local file was byte-identical to upstream. The Spesix reconciliation tree equalled the reviewed remote tree before adding the privacy specification. Innoservice's two foreign register additions remain uncommitted and unchanged. The excluded standalone DHIS2 repository received no commit or push.

Verification: 24 complete requirements Envelopes, unique register entries, no conflict markers and clean diff whitespace checks. The common prototype was rendered and visually inspected at both specified widths, then accepted by the operator after an interactive chat preview on 2026-11-06. These are specification checks, not application tests. Gate #1 is approved; Part B must still be completed by the Orchestrator before dispatch. No implementer is dispatched by this publication.

## Gate #1 records and implementation handoff

The approval recorded above is binding for all 24 work orders, including the two final cleanup orders subject to their migration prerequisites. The existing per-repository register rows remain `planned` until implementation starts; this does not revoke Gate #1.

| Repository | Approved work orders | Approval commit | Publication |
|---|---|---|---|
| django-core-micha | DCM-PRIV-1, DCM-PRIV-2 | `835f4932` | Published |
| ui-core-micha | UCM-PRIV-1, UCM-PRIV-2 | `a4a5178a` | Published |
| hram | HRAM-PRIV-1 | `342c25e5` | Published; the operator explicitly authorised inclusion of the preceding HRAM-DOC-40 documentation commits. |
| Gustav | GUS-PRIV-1 | `0a1b6852` | Published |
| Photogallery | PG-PRIV-1 | `194e451d` | Published |
| Cinevia | CIN-PRIV-1 | `2058f337` | Published |
| maana | MAANA-PRIV-1 | `7dd2f707` | Published |
| Kira | KIRA-PRIV-1 | `89fbb2cf` | Published |
| webshop-guenter | WSG-PRIV-1 | `45866b18` | Published |
| reimbursements | RMB-PRIV-1 | `db611676` | Published |
| jg-ferien | JG-PRIV-1 | `393d8efb` | Published |
| hpc-bridge | HPC-PRIV-1 | `6aef1eb7` | Published |
| fitness-monitor | FM-PRIV-1 | `0a52018c` | Published |
| survey_contact_app | SCA-PRIV-1 | `86271946` | Published |
| kerzenziehen | KZ-PRIV-1 | `a05919f9` | Published |
| bigler-consult | BCO-PRIV-1 | `25d21311` | Published |
| spesix | SPX-PRIV-1 | `f856d47c` | Published |
| survey_app | SVA-PRIV-1 | `403d310a` | Published |
| cockpit | CKP-PRIV-1 | `2c0c02d6` | Published |
| innoservice | INNO-PRIV-1 | `b76fd0e1` | Published |
| yopoulab-hhs-2026 | YOP-PRIV-1 | `0a4995a8` | Published |
| musiknoten | MUS-PRIV-1 | `21fe4546` | Published |

Start the Orchestrator in `C:/Users/Micha Bigler/Documents/webapps`. Pull the target repositories, read the relevant work-order files and follow `orchestrate-codex`. Read current model routing and repository instructions. Complete Part B with the current context package, progress contract and mandatory preamble before any implementer dispatch. Do not treat an Envelope placeholder as a complete coder specification.

Execution order:
1. Verify the released DCM-PRIV-1 contract in django-core-micha on main; its register records completion as 3.22.0 at afab9b1. Reuse the released implementation rather than reimplementing it. Re-read current release state before selecting exact consumer pins.
2. Implement and release UCM-PRIV-1 in ui-core-micha on main against that released contract. Shared visual reference: ui-core-micha@ced7a28; the operator approved the prototype and explanatory copy on 2026-11-06.
3. Deliver BCO-LEGAL-5 in bigler-consult on develop, independently of step 2 where files/contracts do not overlap. Complete its reader/registry interface before BCO-PRIV-1's overlapping document work. Use prototype 319cdd1 and the approved general-text inheritance. BCO-PRIV-1 then adopts the shared services and supplies verified processing disclosures using that interface.
4. Adopt the released shared contracts through each of the 20 listed app work orders on develop, including the footer handoff above. Each app remains independently reviewable; different app repositories may proceed independently after their stated prerequisites. YOP-PRIV-1 keeps its upstream-authoring prerequisite and does not gain an artificial Django/React dependency.
5. Only after all applicable consumer migrations and compatibility checks, implement UCM-PRIV-2, then DCM-PRIV-2, including the consumer pin/migration follow-through specified in their Envelopes.

Use the scoped tests, independent reviews and applicable prototype comparison required by each Envelope and the current governance. Verify actual requests/storage before optional-service activation. Preserve necessary functional data and the separate study/AI/terms permissions. The standalone bottleneck-analysis-app remains excluded. The deferred EU representative issue and separate billing work are not expanded by this rollout. Production promotion and provider-console/operational changes retain their separate gates.

All 24 dated Gate #1 records are published. The HRAM publication sequencing issue was resolved by the operator's explicit permission on 2026-11-06; no shared history was rewritten.
