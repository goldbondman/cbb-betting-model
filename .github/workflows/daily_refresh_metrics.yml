name: Daily refresh metrics (Torvik + Hasla)

on:
  schedule:
    # 10:00 PM PST = 06:00 UTC (next day)
    - cron: "0 6 * * *"
  workflow_dispatch: {}

permissions:
  contents: write

jobs:
  refresh:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          persist-credentials: true

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install deps
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements-refresh.txt

      - name: Refresh CSVs
        env:
          # Replace these with the exact pages you want to scrape, or direct CSV links if you have them.
          TORVIK_URL: "PUT_TORVIK_TABLE_URL_HERE"
          HASLA_URL: "PUT_HASLA_TABLE_URL_HERE"

          # If the relevant table is not the first one on the page, change these.
          TORVIK_TABLE_INDEX: "0"
          HASLA_TABLE_INDEX: "0"
        run: |
          python scripts/refresh_sources.py

      - name: Commit and push if changed
        run: |
          set -e
          if git status --porcelain | grep -E "barttorvik.csv|haslametrics.csv"; then
            git config user.name "github-actions[bot]"
            git config user.email "github-actions[bot]@users.noreply.github.com"
            git add barttorvik.csv haslametrics.csv
            git commit -m "Daily refresh: Torvik + Haslametrics CSV"
            git push
            echo "Pushed updated CSVs."
          else
            echo "No CSV changes to commit."
          fi
