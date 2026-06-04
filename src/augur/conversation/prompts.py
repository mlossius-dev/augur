"""
LLM prompt templates for the conversation layer.
"""

from __future__ import annotations

from augur.conversation.context import ConversationContext

SYSTEM_PROMPT = """\
You are Augur's reasoning assistant.  Augur is a causal graph that tracks \
world events and their relationships.  You answer questions about the current \
state of the world by reasoning over the graph evidence provided.

Rules:
1. Ground every claim in the graph evidence provided.  If evidence is absent \
   or thin, say so explicitly — do not fill gaps from your training data.
2. Cite specific nodes, edges, or signals by name when supporting a claim.
3. Distinguish between conditions that are currently ACTIVE (flagged as \
   active) and those that are inactive or merely present in the graph.
4. Be concise.  Answer in 3-6 sentences unless the question genuinely \
   requires more.  Use plain prose, not bullet lists.
5. If you cannot answer from the evidence, say "The graph does not have \
   sufficient evidence to answer this." rather than speculating.\
"""


def build_context_block(ctx: ConversationContext) -> str:
    """Render graph evidence as a readable block for the LLM."""
    parts: list[str] = []

    if ctx.matched_nodes:
        lines = []
        for n in ctx.matched_nodes:
            state = f" [ACTIVE]" if n["current_state"] == "active" else ""
            desc = f": {n['description'][:100]}" if n["description"] else ""
            lines.append(f"  • {n['name']} ({n['node_type']}){state}{desc}")
        parts.append("=== Relevant graph nodes ===\n" + "\n".join(lines))
    else:
        parts.append("=== Relevant graph nodes ===\n  (no matching nodes found)")

    if ctx.connected_edges:
        lines = []
        for e in ctx.connected_edges:
            lines.append(
                f"  • {e['source_name']} --{e['edge_type'].replace('_',' ')}--> "
                f"{e['target_name']} [{e['weight_band']}]"
                + (f"\n    Reasoning: {e['reasoning']}" if e["reasoning"] else "")
            )
        parts.append("=== Connected causal links ===\n" + "\n".join(lines))

    if ctx.recent_signals:
        lines = [
            f"  • [{s['content_timestamp'][:10]}] ({s['lens_id']}) {s['claim_text']}"
            for s in ctx.recent_signals
        ]
        parts.append("=== Recent signals (last 30 days) ===\n" + "\n".join(lines))

    return "\n\n".join(parts)


def build_messages(
    ctx: ConversationContext,
    history: list[dict],
) -> list[dict]:
    """
    Build the messages list for the LLM.

    history is a list of {role, content} dicts from prior turns.
    The current question is NOT in history yet; it's in ctx.question.
    """
    context_block = build_context_block(ctx)

    messages: list[dict] = []

    # Inject context as the first user turn with a system-style prefix
    messages.append({
        "role": "user",
        "content": (
            f"[Graph evidence for this conversation]\n\n{context_block}\n\n"
            f"[Question]\n{ctx.question}"
        ),
    })

    # If there's prior history, interleave it after the initial context turn
    # This keeps the context fresh for each new turn
    if history:
        # Replace the first user turn's content with just the original question,
        # and prepend a fresh context block at the top
        messages = [
            {
                "role": "user",
                "content": f"[Graph evidence]\n\n{context_block}",
            },
            {
                "role": "assistant",
                "content": "Understood. I have the graph evidence. What would you like to know?",
            },
        ]
        for msg in history:
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": ctx.question})

    return messages
