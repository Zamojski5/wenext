# Consent table fixture

A synthetic consent document in the shape `parse_consent_table` reads: a header, a separator and
eight ordered rows (position, reaction id, run, thread, kind, sha256 of the draft, length). Nothing
here refers to a real run, thread or person.

| # | pilot_reactions.id | run | wątek | rodzaj | sha256(draft) | znaków |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | c0ffee00-0000-0000-0000-000000000001 | run00001 (r/kubernetes) | 1aaaaa1 | niezgoda | 2c50876a87762a0cc71673746844bd39f15bbdcf8a002703e1ba93bef483064b | 55 |
| 2 | c0ffee00-0000-0000-0000-000000000002 | run00002 (r/devops) | 1aaaaa2 | pomoc | 3fcbca1c9707b0331a1763a20c30a839af1e05e8fe6f3717061278245d007059 | 55 |
| 3 | c0ffee00-0000-0000-0000-000000000003 | run00001 (r/kubernetes) | 1aaaaa3 | pomoc | 7b80577fd4dfa378af33bf11ec3705889daf69aab3ad0f6d73bf6d4e5909d3ce | 55 |
| 4 | c0ffee00-0000-0000-0000-000000000004 | run00002 (r/devops) | 1aaaaa4 | niezgoda | 44d77cdadb891bc5639510dcd586669158abcedc46880f0e2437ae6331819b62 | 55 |
| 5 | c0ffee00-0000-0000-0000-000000000005 | run00001 (r/kubernetes) | 1aaaaa5 | pomoc | 2bc75b0adb0850e678c0dfb2ebd096a0cd8ff9750086c0733c4706535c8d916d | 55 |
| 6 | c0ffee00-0000-0000-0000-000000000006 | run00002 (r/devops) | 1aaaaa6 | pomoc | 8cb9b305d318b4f3bc8ffa439c1507427bbbf090429cde2ba185db95703b9cf3 | 55 |
| 7 | c0ffee00-0000-0000-0000-000000000007 | run00001 (r/kubernetes) | 1aaaaa7 | pomoc | 4766c9fe3a64faed659502f5ee30308c5b825a5f781e733cd265bc004f783775 | 55 |
| 8 | c0ffee00-0000-0000-0000-000000000008 | run00002 (r/devops) | 1aaaaa8 | pomoc | 6448f821a1621e59611ce18319afd3ef86f61f9bb00d99fd05fb49f80d8f57d5 | 55 |
