# SNU Extracurricular Crawler

Crawler for Seoul National University's extracurricular program site.

By default, the crawler only writes programs that appear to allow foreign or
international students. It keeps entries that explicitly mention foreign
students and, unless strict mode is enabled, entries whose eligibility text says
all students or all members are eligible.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

## Run

```bash
python crawl_snu_extra.py --max-pages 2 --delay 1.5
```

The default outputs are:

- `snu_extra_programs.json`
- `snu_extra_programs.csv`
- `snu_extra_programs.md`

The Markdown output includes clickable program-title links and clickable
homepage links when a homepage is listed.

To show the browser while debugging:

```bash
python crawl_snu_extra.py --max-pages 2 --headed
```

To export every program, not just foreigner-eligible ones:

```bash
python crawl_snu_extra.py --include-all
```

To keep only entries that explicitly mention foreign or international students:

```bash
python crawl_snu_extra.py --strict-foreigner-match
```
