# Security

ShowCheck reads show files that may come from people you do not know. It never extracts them to disk and never
executes anything in them, and it rejects malformed or oversized files with a clear error (see "Handling untrusted
files" in `README.md`).

## Reporting a problem

If a file makes ShowCheck crash, hang, use far too much memory, show a Python traceback, or touch anything outside
its output folder, please tell us:

- **GitHub:** open an issue at https://github.com/forgottenl5/showcheck/issues. **Do not attach the file** (it may be
  harmful to others, or contain someone's real show). Describe what you ran, what happened and the ShowCheck commit,
  and how the file was built if you know.
- **Email:** forgotteni5.lab@gmail.com, for anything you would rather not post publicly. We can arrange a private way
  to share the file if we need it.

Please give us a reasonable time to fix a problem before you publish details of it.

## Scope

In scope: the readers (`.skyc`, `.vviz`, venue and manifest JSON), the CLI, and the reports it writes.
Out of scope: whether a check's verdict is right for your show. That is a correctness report, and normal issues are
welcome for it. ShowCheck is not a safety certification.
