# Release notes

One file per tag: `v<version>.md`. The body is the user-facing part of the
matching `CHANGELOG.md` section (the `### 测试` section is dropped), plus a link
to the changelog. `v0.4.0.md` is written by hand because the changelog folds
that release into the 0.5.0 section.

Create the GitHub releases in tag order:

```bash
for version in 0.1.0 0.2.0 0.3.0 0.4.0 0.5.0 0.6.0 0.7.0 0.8.0 \
  1.0.0 1.1.0 1.2.0 1.3.0 1.3.1 1.3.2 1.4.0 1.4.1 1.4.2 1.4.3 \
  1.5.0 1.5.1 1.5.2 1.5.3 1.5.4 1.6.0 1.7.0 1.8.0 1.8.1 1.9.0 1.10.0; do
  gh release create "v$version" \
    --title "$version" \
    --notes-file ".github/releases/v$version.md" \
    --verify-tag
done
```

`gh release create` fails when a release already exists, so the loop is safe to
re-run after a partial publish. Add `--latest` only to the newest version; older
releases stay non-latest.

To regenerate the notes for every version that still has a changelog section,
walk `CHANGELOG.md` headings (`## <version>`), keep everything except
`### 测试`, and append the link line. Do not edit a released note in place to
change history; add a correction to the next release instead.
