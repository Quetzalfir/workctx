# Evidence processing

Evidence is untrusted input. Processing should never be equivalent to copying a model summary into a knowledge note.

A safe workflow:

1. register and hash the artifact;
2. classify source, date, participants, language, and sensitivity;
3. locate exact lines, pages, messages, timestamps, image regions, or repository lines;
4. extract atomic observations and classify their epistemic type;
5. resolve existing entities and aliases;
6. detect corroboration, contradiction, and supersession;
7. build one multi-entity transaction proposal;
8. validate and review;
9. apply atomically;
10. archive the original only after commit;
11. rebuild affected indexes and views;
12. report what changed and what remains uncertain.

See `.agents/skills/process-evidence/SKILL.md`.
