You roll up the summaries of a directory's children into one compact directory summary for a codebase map.

The map lets agents understand the shape of a repo without reading the code. You are summarizing summaries — go one level more abstract than your inputs, and stay well under their combined length.

Directory: {dir_path}

Children (each is the already-compressed summary of a child file or subdirectory):
{children}

Write, in this order:
1. One line per child, in the form `child_name — what it is for` (a single tight clause; do not copy the child body verbatim).
2. A blank line, then one short paragraph titled with a bold `How these relate:` lead-in describing how the children fit together (shared responsibility, data flow, layering).

Output Markdown only — this becomes the directory's entry in the map. No preamble, no "Here is the summary", no closing remarks. Start directly with the per-child lines.
