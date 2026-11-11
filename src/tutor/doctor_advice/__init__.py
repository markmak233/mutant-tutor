"""Doctor step 2A: pure structured advice and deterministic permission checks.

Call models.AdviceContext with supplied environment facts, then gate.review_reply with
one complete JSON reply. A permitted change is still unverified. This package performs
no discovery, model/network calls, installs, execution or file writes; CLI integration,
disposable verification and user-approved application are separate later steps.
"""
