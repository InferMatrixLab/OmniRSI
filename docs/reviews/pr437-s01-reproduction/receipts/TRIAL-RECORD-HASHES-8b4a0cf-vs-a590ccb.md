# Trial-record byte identity: `8b4a0cf` vs `a590ccb`

- **After (new local merge head):** `8b4a0cfbde8a1f726c2ec1dcc4886cbb5f773db3` (ordinary merge of `9218391` + current main `57f8e8a4915b38201d9fa58d4d05acbe3c4199ee`; local only, not pushed).
- **Before (pre-S01 baseline / merge-base):** `a590ccb25596396a592f237105166ade4aedeaaa`.
- **Platform:** Linux, Debian GNU/Linux 13.7, Python 3.13.5, isolated `git archive` copies, shared `--work` directory so host path fields match. This is not the 2026-11-13 `e51e8c64` / `8150ee0` table, and not the retained `9218391` vs `a590ccb` Windows table.
- **Host timestamps (Asia/Shanghai, UTC+8):** see `CAPTURE.log` in this directory.

trial records: before=58 after=58
all retained files: before=448 after=448
tree digest over all retained files: before=7457bb7d4b998be31f64d4e9cb25e4e21e0b8bcf21ab8bdd1669496f9f616f61
tree digest over all retained files: after =7457bb7d4b998be31f64d4e9cb25e4e21e0b8bcf21ab8bdd1669496f9f616f61
tree digest over trial result.json:  before=9302f92978c0d97f56e6ebfff463a7aa0ef062c7788b88e75a6d4277c042f782
tree digest over trial result.json:  after =9302f92978c0d97f56e6ebfff463a7aa0ef062c7788b88e75a6d4277c042f782

| Trial | Verdict | evaluation_error | result.json SHA-256 before | after | Same |
| --- | --- | --- | --- | --- | --- |
| `audit-client-incorrect` | FAIL | none | `70c52da4aa86dc04f24e3e0a977e94c9108224d124e4306e82819a4204675d27` | `70c52da4aa86dc04f24e3e0a977e94c9108224d124e4306e82819a4204675d27` | yes |
| `audit-client-invalid_json` | INCONCLUSIVE | invalid_output_json | `911d37856de13c47e683a9b7a502997a34bec107449015844a09dec0caa79322` | `911d37856de13c47e683a9b7a502997a34bec107449015844a09dec0caa79322` | yes |
| `audit-client-invalid_trace` | INCONCLUSIVE | agent_trace_error | `bb8c0d6a6a0641eaaf1da0671744f6c6798fe4250e20d555018f5b2c000b1024` | `bb8c0d6a6a0641eaaf1da0671744f6c6798fe4250e20d555018f5b2c000b1024` | yes |
| `audit-client-missing_output` | INCONCLUSIVE | missing_output | `87c7af24ba52d1076422420370b5196f9c59d7ff0f8ffc3ce2c581f842202d04` | `87c7af24ba52d1076422420370b5196f9c59d7ff0f8ffc3ce2c581f842202d04` | yes |
| `audit-client-nonobject` | FAIL | none | `94b860c4ead27b96c955915991a32a21dd5c739fbb1f156368efbd94134c2806` | `94b860c4ead27b96c955915991a32a21dd5c739fbb1f156368efbd94134c2806` | yes |
| `audit-client-nonzero` | INCONCLUSIVE | agent_failed | `a53dcf7c5f170330f8cb780067f5dd04b83f2aef8bee5ca5797f23c0590a21ac` | `a53dcf7c5f170330f8cb780067f5dd04b83f2aef8bee5ca5797f23c0590a21ac` | yes |
| `audit-client-null` | FAIL | none | `1de17286f6572b84617d2b3cb11aa273d7cb5d6be163a854c65362b61e0da4b2` | `1de17286f6572b84617d2b3cb11aa273d7cb5d6be163a854c65362b61e0da4b2` | yes |
| `audit-client-partial_usage` | PASS | none | `4ccbeaa36e03588a6b31c62cb474b4c9357f95f4628fba3da667e419b33201ac` | `4ccbeaa36e03588a6b31c62cb474b4c9357f95f4628fba3da667e419b33201ac` | yes |
| `audit-client-timeout` | INCONCLUSIVE | agent_timeout | `645515e081cb17275e9cffcfcdf7fd1c886d2ec45c299669a831af53443a9270` | `645515e081cb17275e9cffcfcdf7fd1c886d2ec45c299669a831af53443a9270` | yes |
| `audit-client-unknown_usage` | PASS | none | `88b98ab8599474b7a5e4db2c9afe723bae399eb34d3635f4dac7f1cfbd242622` | `88b98ab8599474b7a5e4db2c9afe723bae399eb34d3635f4dac7f1cfbd242622` | yes |
| `audit-invalid-input-report` | INCONCLUSIVE | missing_verifier_result | `5a5365d4f26e1d52aa25d6969d5026978ed433514dd6936c27e684a836de9981` | `5a5365d4f26e1d52aa25d6969d5026978ed433514dd6936c27e684a836de9981` | yes |
| `audit-pass-equal-threshold-E-benchmark` | PASS | none | `6c6b91d4a3aaa0b00dd86fa59c7c7516a84dc38f4cc7e90db52f06c0c7ec1174` | `6c6b91d4a3aaa0b00dd86fa59c7c7516a84dc38f4cc7e90db52f06c0c7ec1174` | yes |
| `audit-pass-equal-threshold-N` | PASS | none | `c00a07b5ca5be38c3369f92c3fe1bd5ade38b22c33f04753742453d24c1480fe` | `c00a07b5ca5be38c3369f92c3fe1bd5ade38b22c33f04753742453d24c1480fe` | yes |
| `audit-pass-kernel-only-E-benchmark` | PASS | none | `baafdf327740b728af51b16575546dd33262e0627de6b4cfb64881e69663de09` | `baafdf327740b728af51b16575546dd33262e0627de6b4cfb64881e69663de09` | yes |
| `audit-pass-kernel-only-N` | PASS | none | `113045e6abe5edc37afe63c4fb64fa4c6f4f278bf02a6c6166553eb4ae3e7468` | `113045e6abe5edc37afe63c4fb64fa4c6f4f278bf02a6c6166553eb4ae3e7468` | yes |
| `audit-pass-quality-failed-E-benchmark` | PASS | none | `bff96c0d9c092d4bc913856c6eb4ee33a24016eda860ea13a66b7414ed578864` | `bff96c0d9c092d4bc913856c6eb4ee33a24016eda860ea13a66b7414ed578864` | yes |
| `audit-pass-quality-failed-N` | PASS | none | `a7a41694f0b8fe51adae25fe6910f5f9c20833a0f7b9465c6533792f512ac921` | `a7a41694f0b8fe51adae25fe6910f5f9c20833a0f7b9465c6533792f512ac921` | yes |
| `audit-verifier-empty_assertions` | INCONCLUSIVE | invalid_verifier_result | `8aef0d0be5e3257dbacd76d000410ddfac788fac601c4d1b2741793a64dc6ee9` | `8aef0d0be5e3257dbacd76d000410ddfac788fac601c4d1b2741793a64dc6ee9` | yes |
| `audit-verifier-exception` | INCONCLUSIVE | missing_verifier_result | `a0192e43a80f82b18b05ac084528e2952e29afee8d0982e22903073d7e6cd3c6` | `a0192e43a80f82b18b05ac084528e2952e29afee8d0982e22903073d7e6cd3c6` | yes |
| `audit-verifier-inconsistent` | INCONCLUSIVE | invalid_verifier_result | `31d4c585d10dbf7c385ba4de3d77f1c856f24901f9cf891600871749b3f06d5d` | `31d4c585d10dbf7c385ba4de3d77f1c856f24901f9cf891600871749b3f06d5d` | yes |
| `audit-verifier-infinite_assertion` | INCONCLUSIVE | invalid_verifier_result | `91b466a7622f1ddb9a9213687b14310f8fd203684e2ac443d9a195fce9284ee1` | `91b466a7622f1ddb9a9213687b14310f8fd203684e2ac443d9a195fce9284ee1` | yes |
| `audit-verifier-invalid_json` | INCONCLUSIVE | invalid_verifier_result | `8156ca67b89ac475b713fbe28269bf22d1d23cb5cdd76e69c1378285e2c0ebda` | `8156ca67b89ac475b713fbe28269bf22d1d23cb5cdd76e69c1378285e2c0ebda` | yes |
| `audit-verifier-missing` | INCONCLUSIVE | missing_verifier_result | `011771e608505efd8d43481ab486644e23cbee0356399abf3b141d48aed4b469` | `011771e608505efd8d43481ab486644e23cbee0356399abf3b141d48aed4b469` | yes |
| `audit-verifier-missing_assertions` | INCONCLUSIVE | invalid_verifier_result | `fab2bfe99de5030e036e6a0915677566f3916b29101384daeaca8b234e3c38c2` | `fab2bfe99de5030e036e6a0915677566f3916b29101384daeaca8b234e3c38c2` | yes |
| `audit-verifier-missing_passed` | INCONCLUSIVE | invalid_verifier_result | `0e4293189fcda0fd05fa66da124bb1b9857b9c4b2ed19079ca34dbc2045d98ef` | `0e4293189fcda0fd05fa66da124bb1b9857b9c4b2ed19079ca34dbc2045d98ef` | yes |
| `audit-verifier-nan_assertion` | INCONCLUSIVE | invalid_verifier_result | `d95f8ecae98316a9c0da5b06c20e9e186f78cc9bef0e734e562a15bc6c3b4282` | `d95f8ecae98316a9c0da5b06c20e9e186f78cc9bef0e734e562a15bc6c3b4282` | yes |
| `audit-verifier-nonobject` | INCONCLUSIVE | invalid_verifier_result | `67666e9c4de3184e23c58bd0fcc5e6b61e950af61d7a7a73201c0e08104edb4a` | `67666e9c4de3184e23c58bd0fcc5e6b61e950af61d7a7a73201c0e08104edb4a` | yes |
| `audit-verifier-overflow_assertion` | INCONCLUSIVE | invalid_verifier_result | `4caa8a813055a8bd7551af61e0079f73ad03dc0d10a88f95ba2950c720d75c2a` | `4caa8a813055a8bd7551af61e0079f73ad03dc0d10a88f95ba2950c720d75c2a` | yes |
| `audit-verifier-string_assertion` | INCONCLUSIVE | invalid_verifier_result | `ff3360a1377db2cd07ba4ff9f3aee240581b05772278d25b0011732b80a1fa27` | `ff3360a1377db2cd07ba4ff9f3aee240581b05772278d25b0011732b80a1fa27` | yes |
| `audit-verifier-string_passed` | INCONCLUSIVE | invalid_verifier_result | `610670386097b0a3b176e7c93383526aae18915032d67ffe13b0853925590d66` | `610670386097b0a3b176e7c93383526aae18915032d67ffe13b0853925590d66` | yes |
| `audit-verifier-wrong_exit` | INCONCLUSIVE | verifier_exit | `6324c353e16c88879038e6c3df325aa1484ae47a4a67faf393ed025e02aa5c06` | `6324c353e16c88879038e6c3df325aa1484ae47a4a67faf393ed025e02aa5c06` | yes |
| `diagnostic-fail-case-01` | FAIL | none | `1f406008b31144028c77a6881c2d2003d0838a03416d5471a07f30880b616e08` | `1f406008b31144028c77a6881c2d2003d0838a03416d5471a07f30880b616e08` | yes |
| `diagnostic-fail-case-02` | FAIL | none | `9c836a9f6cc47022b68cf7570689017eb8673687f3ba828d5d6256251df21539` | `9c836a9f6cc47022b68cf7570689017eb8673687f3ba828d5d6256251df21539` | yes |
| `diagnostic-fail-case-03` | FAIL | none | `cd14caf267245a937b5430a7786d1ff83ac62179a32f4b0408c2120d3b016d1b` | `cd14caf267245a937b5430a7786d1ff83ac62179a32f4b0408c2120d3b016d1b` | yes |
| `diagnostic-fail-case-04` | FAIL | none | `c0bc4266e6327eac2378327b374727b2cbc30d43dbf519902bc6750e63dd291b` | `c0bc4266e6327eac2378327b374727b2cbc30d43dbf519902bc6750e63dd291b` | yes |
| `diagnostic-fail-case-05` | FAIL | none | `fbd468eda32e800438c7daf9982560f02e857ee94d82051f5eeeace4b891e0c7` | `fbd468eda32e800438c7daf9982560f02e857ee94d82051f5eeeace4b891e0c7` | yes |
| `diagnostic-fail-case-06` | FAIL | none | `0ae10f1bdc9019d63c2eec2ddd0db1c834f96b9b877e0d912c1ecb45c43418a4` | `0ae10f1bdc9019d63c2eec2ddd0db1c834f96b9b877e0d912c1ecb45c43418a4` | yes |
| `diagnostic-fail-case-07` | FAIL | none | `2dc884c7839b3271906ae9a1a6f6c910f71ecd2de1329282ba6a0ec629d53d96` | `2dc884c7839b3271906ae9a1a6f6c910f71ecd2de1329282ba6a0ec629d53d96` | yes |
| `diagnostic-fail-case-08` | FAIL | none | `532196d9188a3b8e1b43c6392cc9fae68b09194c0d624b7431173dd7883937a3` | `532196d9188a3b8e1b43c6392cc9fae68b09194c0d624b7431173dd7883937a3` | yes |
| `diagnostic-fail-case-09` | FAIL | none | `b6bc094f7baeaf4c4e9617792cd6ab0154375e3cacc98cad7186b867b0cdbc5a` | `b6bc094f7baeaf4c4e9617792cd6ab0154375e3cacc98cad7186b867b0cdbc5a` | yes |
| `diagnostic-fail-case-10` | FAIL | none | `0623d465a3b49e71b3d4af01e21cc53b1461ba242f382756561d6d69f6fcaac8` | `0623d465a3b49e71b3d4af01e21cc53b1461ba242f382756561d6d69f6fcaac8` | yes |
| `diagnostic-pass-case-01` | PASS | none | `ec1d82202b6e9ff968031dbd148447b876019bd575fb50e5601b9668bfbba606` | `ec1d82202b6e9ff968031dbd148447b876019bd575fb50e5601b9668bfbba606` | yes |
| `diagnostic-pass-case-02` | PASS | none | `0180d7c5b2880dee7742ac253f8622ca83929ee905fef5b68994bdfcd9e286a0` | `0180d7c5b2880dee7742ac253f8622ca83929ee905fef5b68994bdfcd9e286a0` | yes |
| `diagnostic-pass-case-03` | PASS | none | `c4120e1e1fd3734a8b5e9a47908628c732496eb080b05b56a7fa24cce98b0d08` | `c4120e1e1fd3734a8b5e9a47908628c732496eb080b05b56a7fa24cce98b0d08` | yes |
| `diagnostic-pass-case-04` | PASS | none | `63c672dba0bf7be8e17efbb25e3b3262bd14e57edf9e250dba3ef5d56a06295d` | `63c672dba0bf7be8e17efbb25e3b3262bd14e57edf9e250dba3ef5d56a06295d` | yes |
| `diagnostic-pass-case-05` | PASS | none | `b4a96aeae6d84bba172b5404dd3e55aad84f9bafee6fea1497454fb5198d636e` | `b4a96aeae6d84bba172b5404dd3e55aad84f9bafee6fea1497454fb5198d636e` | yes |
| `diagnostic-pass-case-06` | PASS | none | `4e13b1f7b58670af3353dd632268c18f32d4ae8e7b3a52565db0ac5b06789424` | `4e13b1f7b58670af3353dd632268c18f32d4ae8e7b3a52565db0ac5b06789424` | yes |
| `diagnostic-pass-case-07` | PASS | none | `1059b955e0008356fbaf28aaf546f7a40802e9168a83a1ce1a77dfbe48d48496` | `1059b955e0008356fbaf28aaf546f7a40802e9168a83a1ce1a77dfbe48d48496` | yes |
| `diagnostic-pass-case-08` | PASS | none | `c3511bcc506da411b721113843ea07b0dbff72011b8d4ae7b935814a26e9adee` | `c3511bcc506da411b721113843ea07b0dbff72011b8d4ae7b935814a26e9adee` | yes |
| `diagnostic-pass-case-09` | PASS | none | `d7a96137d2fd351ced21eea55ac480fd8dae23534e020ed199ff906c9fd74127` | `d7a96137d2fd351ced21eea55ac480fd8dae23534e020ed199ff906c9fd74127` | yes |
| `diagnostic-pass-case-10` | PASS | none | `9573da88340640d38fa546ce6914643a60d330c23198baab955a3d2bc69c6665` | `9573da88340640d38fa546ce6914643a60d330c23198baab955a3d2bc69c6665` | yes |
| `engineering-invalid-answer` | INCONCLUSIVE | invalid_artifact_answer | `b938a2525a93ec5542a8b87e7f0e4d6c2fa038f423746d53e525612f84593e91` | `b938a2525a93ec5542a8b87e7f0e4d6c2fa038f423746d53e525612f84593e91` | yes |
| `engineering-missing-artifact` | INCONCLUSIVE | missing_or_invalid_artifact | `19a847925ba6db1eba0a667c211feb1c4f9e51e90c41d5ca4bc9ee5ff5d04c4c` | `19a847925ba6db1eba0a667c211feb1c4f9e51e90c41d5ca4bc9ee5ff5d04c4c` | yes |
| `engineering-sandbox-unavailable` | FAIL | none | `5e88a45ded3c2089f447a7d60225ffa5579488ab6ce408047b0dd5c4ccbfa438` | `5e88a45ded3c2089f447a7d60225ffa5579488ab6ce408047b0dd5c4ccbfa438` | yes |
| `engineering-verified-fail` | FAIL | none | `641b4acd36b481343de6009d7428db1b381309921e582db938b0715f53b17a6c` | `641b4acd36b481343de6009d7428db1b381309921e582db938b0715f53b17a6c` | yes |
| `engineering-verified-pass` | PASS | none | `3a91e35131aafec0640a92307b01c2bc66563e3fcf4be48887794f5dcb83b91d` | `3a91e35131aafec0640a92307b01c2bc66563e3fcf4be48887794f5dcb83b91d` | yes |
| `generation-deferred` | INCONCLUSIVE | artifact_verification_deferred | `1cae71eae84476d4ab8723fef0e442e50bc22ea99e3cb59bfba3bdff107d88c5` | `1cae71eae84476d4ab8723fef0e442e50bc22ea99e3cb59bfba3bdff107d88c5` | yes |
| `generation-invalid` | INCONCLUSIVE | invalid_generated_artifact | `4110c9f24551c378a974526db0d9c608048591096f0374f0ad33c5f3071c791b` | `4110c9f24551c378a974526db0d9c608048591096f0374f0ad33c5f3071c791b` | yes |

| Pure judgment output | SHA-256 before | after | Same |
| --- | --- | --- | --- |
| `pure/constants.json` | `45141fa9fcfb7d0819a5e148af612aba24f343840edd231984b52002c3e50f7e` | `45141fa9fcfb7d0819a5e148af612aba24f343840edd231984b52002c3e50f7e` | yes |
| `pure/diagnostic.json` | `ecf7ec4a4afa7509b93eb3534db2760adbbff350c83c5922c1365bf8ef0fc0a0` | `ecf7ec4a4afa7509b93eb3534db2760adbbff350c83c5922c1365bf8ef0fc0a0` | yes |
| `pure/engineering.json` | `38b401aeac01085a7cb89ad649c51b702e22f59318ce6241f0f3e0668b145f62` | `38b401aeac01085a7cb89ad649c51b702e22f59318ce6241f0f3e0668b145f62` | yes |
| `pure/improvement.json` | `8e1e0ec643a683dabf0789ba26387c98a058630921e7c5285fc718a819f9fbd7` | `8e1e0ec643a683dabf0789ba26387c98a058630921e7c5285fc718a819f9fbd7` | yes |
| `pure/summarize_synthetic.json` | `f1a02615bdb708dbdad447e4b0fb6ad6d552b2287292673cf68a4ca4e9ee0378` | `f1a02615bdb708dbdad447e4b0fb6ad6d552b2287292673cf68a4ca4e9ee0378` | yes |
| `pure/summarize_trial_records.json` | `e7756f567e0f2bd26dc559ac5a1171985466af8b0fedf503563456ba870869bc` | `e7756f567e0f2bd26dc559ac5a1171985466af8b0fedf503563456ba870869bc` | yes |
