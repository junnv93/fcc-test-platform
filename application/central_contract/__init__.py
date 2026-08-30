"""Central API wire vocabulary — the layer both lanes speak, owned by neither.

`shared-kernel` owns this package. It is not the domain layer, and that is why
the lane's purpose no longer says "the hexagonal domain layer": the shared kernel
is *the layers more than one lane reads*, and as of 2026-08-28 there are two.

WHY THIS CLOSURE AND NOT THE REST OF `application/platform/`
------------------------------------------------------------
Membership is a measured import closure, not a taste: the route/operation/DTO
table of the central API, plus what that table reaches. Widening past that fixed
point would hand a joining provider platform *services* — the outcome ADR-0018
D-5 keeps closed — and narrowing it would leave a name the delivered box cannot
resolve.

⚠️ THE MEMBERS ARE NOT LISTED HERE, AND THAT IS DELIBERATE. This paragraph used
to name four modules. The 2026-08-29 decomposition made that sentence false
without changing one thing it argued — the closure is the same closure, the
table is merely no longer one file. A membership list in prose goes stale on the
next decomposition and reads as fact while it is wrong, which is the failure this
repository keeps paying for. The root is a DIRECTORY in the extraction manifest,
so the members are derived from it; count them there, not here.

WHY `api_contracts` IS A FACADE
--------------------------------
Since 2026-08-29 `api_contracts` assembles and re-exports; the route, permission,
schema and operation declarations live in `surface_*` modules and the
surface-crossing vocabulary in `api_vocabulary` / `api_parameters` /
`api_operation_factory` / `api_request_validation`. The boundary is the
*operation surface*, not the table kind — measured on 82 commits, a contract
change touches exactly one surface 92% of the time, while a table-kind split
would make every added endpoint touch five tables at once (the tables are
parallel arrays keyed by the same operationId). Public names did not change: the
56 import sites that read this package were not edited, and that was the
condition of the decomposition, not a convenience.

WHY THE NAME STOPPED SAYING `platform`
---------------------------------------
Operator judgement B-2 (2026-08-28): *the session node stays in
fcc-unlicensed-headless; what leaves is the vocabulary it speaks to central.* A
module the platform lane no longer owns should not be addressed as
`application.platform.*` — the old name would say the lane still owns it, and the
next reader would believe it.
"""
