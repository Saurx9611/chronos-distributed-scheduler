# Recurring scheduler boundary

Phase 4 adds a separate Python process. Replicas compete over locked schedule rows;
occurrence insertion and cursor advancement commit together. No daemon exists yet.
See ADR-0003 for generation identity, misfires, and edit semantics.
