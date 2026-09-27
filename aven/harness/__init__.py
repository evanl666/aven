"""The harness: what an agent is, with no opinion about what it is for.

Nothing in here knows whether it is helping with files, a calendar or a
codebase. It knows how a conversation is recorded, when the model is asked,
what a tool is, and what may take effect without being confirmed. An app
supplies the rest.

Two rules keep that true, and both are enforced by tests:

- no import from `aven.apps`, `aven.terminal` or `aven.toolkit`
- no text a person will read, in any language
"""
