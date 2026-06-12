You write a dense, faithful summary of a single source file for a codebase map.

The map lets agents understand the shape of a repo without reading the code, so the
summary must be accurate and compressed.

File path: {path}

Symbols (ground truth — already extracted from the parse tree):
{symbols}

When symbols are listed above, treat them as authoritative. Summarize every function,
class, method, object, and notable constant. Do not invent, omit, or alter signatures —
reproduce each signature exactly as given, then add a one-line statement of its intent.
Note any cross-file relationship worth knowing (for example "calls X", "implements
protocol Y", "subclasses Z"). When no symbols were supplied (no programmatic extractor),
read the source below and summarize every symbol it defines yourself.

Hard limit: stay strictly under {budget_tokens} tokens. Compress harder for big files;
tiny files may be near-verbatim. Going over the limit is a failure.

Output Markdown only — this becomes the file's entry in the map. No preamble, no
"Here is the summary", no closing remarks. Start directly with the content.

Source:
{source}
