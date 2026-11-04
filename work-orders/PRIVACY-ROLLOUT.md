# Privacy services and application rollout

Status: specifications authored, implementation approval pending. Date: 2026-11-04.

## Decisions and scope

The operator requested work orders for shared dcm/ucm services and **all apps**, based on Sol's read-only source inventory. Explicit decisions: prior opt-in for optional services uniformly across CH/EU; visit measurement only after choice; one common visual prototype. The scope is the 20 repositories in webapp-management/recovery/staging/adoption-inventory.json (20 apps). All checkouts were present. The standalone DHIS2 repository is explicitly excluded by the operator. The staging catalogue is a scope baseline, not proof that all current production deployments or edge-injected services were observed.

No feature implementation or production activation was approved by these requirements answers. Gate #1 remains pending for each WO. The requested branch repairs are separately authorised; they are not privacy-feature approval.

## Dependency order

1. DCM-PRIV-1 supplies public-safe manifest, notice references, anonymous/browser-scoped consent receipts and enforcement.
2. UCM-PRIV-1 supplies common UI and gated lifecycle against that contract.
3. Each app adoption below is independently reviewable and retains its controller and functional data. No release is automatically rolled across apps.
4. UCM-PRIV-2 removes old UI/adapters after caller migration; DCM-PRIV-2 removes the old profile fields with concrete consumer migrations after UI adoption. Neither removal is complete without its app follow-through.

The static report follows the same privacy principles without acquiring a Django backend or a fake banner. No app is required to add an optional purpose that it does not currently need.

## Work orders

| Repository | Work order | Outcome | Publication |
|---|---|---|---|
| django-core-micha | [DCM-PRIV-1](../../django-core-micha/work-orders/DCM-PRIV-1.md) | Shared privacy information and purpose-specific consent | Prepared; publication recorded below |
| ui-core-micha | [UCM-PRIV-1](../../ui-core-micha/work-orders/UCM-PRIV-1.md) | Shared privacy choices and notice surfaces | Prepared; publication recorded below |
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

Pending final repository commits and pushes. No feature is implemented by these specification files.

