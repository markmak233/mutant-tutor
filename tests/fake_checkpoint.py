"""Real synthetic SQLite checkpoint fixtures; no empty files masquerading as saved state."""

from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.sqlite import SqliteSaver


def write_checkpoint(draft, values):
    """Append a root checkpoint with the same thread identity as the production CLI."""
    draft.mkdir(parents=True, exist_ok=True)
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = values
    with SqliteSaver.from_conn_string(str(draft / "checkpoint.sqlite")) as saver:
        saver.put({"configurable": {"thread_id": draft.name, "checkpoint_ns": ""}},
                  checkpoint, {}, {})
