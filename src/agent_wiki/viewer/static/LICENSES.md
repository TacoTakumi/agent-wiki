# Third-party stylesheets

The viewer inlines one vendored stylesheet into every page. It is MIT
licensed; its notice follows.

| File | Package | Version | Source |
|---|---|---|---|
| github-markdown.css | github-markdown-css | 5.9.0 | https://cdn.jsdelivr.net/npm/github-markdown-css@5.9.0/github-markdown.css |

Changes from upstream:

- The upstream file has no licence header comment. The vendored file gets
  one added first line that names the package, version, licence and
  copyright holder, says the file is modified, and points to this file.
- The file's two colour sets (the `prefers-color-scheme: dark` and
  `prefers-color-scheme: light` blocks) are merged into one set of
  light-dark() pairs on `.markdown-body`, so the rendered body follows the
  viewer's theme through the inherited `color-scheme`. Each pair is
  `light-dark(<upstream light>, <upstream dark>)`; a value that is the same in
  both sets stays that one value. The core colours point at the viewer tokens
  instead: `--fgColor-default`, `--fgColor-muted`, `--fgColor-accent`,
  `--bgColor-default`, `--bgColor-muted`, `--borderColor-default` and
  `--borderColor-muted` use `var(--fg)`, `var(--fg-muted)`, `var(--accent)`,
  `var(--bg)`, `var(--bg-muted)`, `var(--border)` and `var(--border-muted)`
  from `viewer.css`. The `color-scheme: dark` and `color-scheme: light`
  declarations, the `[data-theme]` selectors and the `--fgColor-accent:
  Highlight` fallback (only used when neither query matched) are removed.
  The rest of the file is byte-identical to upstream.
- No rules were removed. The file has no `@font-face` rule, no `@import` and
  no `url()` to a remote host. It has two `url()` values (the heading anchor
  link icon mask); both are inline `data:` SVG images, so nothing is fetched.

`viewer.css` is this project's own file: the page frame, built on its own
light and dark colour tokens.

## github-markdown-css 5.9.0

```
MIT License

Copyright (c) Sindre Sorhus <sindresorhus@gmail.com> (https://sindresorhus.com)

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```
