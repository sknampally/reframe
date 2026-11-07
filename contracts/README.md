# Record templates

These YAML files are the source of the required fields. `reframe` loads them and blocks a stage when the workspace record for that stage is missing a required field or evidence link. The schema is unchanged. A stage that has no record file does not invent one. The separate [pilot runner](../docs/running-a-pilot.md) still checks its own narrower JSON task manifest. Replace every `REQUIRED` value before use.

- [Task contract](task-contract.yaml): scope, baseline, permissions, acceptance criteria, and budget.
- [Review record](review-record.yaml): exact output revision, reproducible evidence, and blocking findings.
- [Approval record](approval-record.yaml): explicit human decision for one action and revision.

Keep populated operational records in an approved private location. Public examples must be synthetic and must never include private storage paths, account identifiers, access links, or raw tool outputs. Linking to a private artifact from a public file can itself disclose sensitive metadata.
