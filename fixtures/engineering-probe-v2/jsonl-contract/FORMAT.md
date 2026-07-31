# Records JSON Lines Format

Each non-empty line is one complete JSON object. Writers must preserve every
field in each record, emit non-ASCII text directly, and terminate non-empty
output with a newline. Readers must accept embedded newlines and delimiters
inside JSON string values without changing them. Empty input represents an
empty record sequence.
