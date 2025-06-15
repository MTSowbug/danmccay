# Dan McCay MUD Agent

This repository contains experimental tools for interacting with the *Cleft of Dimensions* MUD and for fetching RSS feeds.

## Setup

1. **Clone the repository**
   ```bash
   git clone <repo-url>
   cd danmccay
   ```

2. **Install Miniconda/Anaconda** (if not already installed). See [conda.io](https://docs.conda.io/en/latest/miniconda.html).

3. **Create the conda environment**
   ```bash
   conda env create -f environment.yml
   conda activate mccay
   ```

   Alternatively, you can create it manually:
   ```bash
   conda create -n mccay python=3.12 pandas numpy pyyaml
   conda activate mccay
   pip install openai anthropic feedparser dill strip-ansi
   ```

4. **Set API keys** for services you intend to use:
   ```bash
   export OPENAI_API_KEY=<your key>
   export ANTHROPIC_API_KEY=<your key>
   ```

5. **Run the scripts**
   ```bash
   python codagent_mccay.py        # main MUD agent
 python feedfetchtest.py rss     # update articles.json from RSS feeds
 python feedfetchtest.py pdf     # download PDFs for stored articles
  ```

## Testing

Run the unit tests with `pytest`:

```bash
pytest -q
```

The suite covers both the RSS fetcher and helper functions in `codagent_mccay`.

Tests are automatically executed by GitHub Actions on every push and pull request.

### Running continuously

`codagent_mccay.py` can run as a background daemon so the agent stays
connected even after you log out. Use the `--daemon` flag:

```bash
python codagent_mccay.py --daemon --logfile mccay.log --pidfile mccay.pid
```

The process detaches from the terminal and writes output to the log file
specified by `--logfile`. The process ID is stored in `--pidfile` so you
can stop the agent later with `kill $(cat mccay.pid)`.

## Files

- `codagent_mccay.py` – telnet-based MUD agent that relies on OpenAI/Anthropic models.
- `feedfetchtest.py` – fetch RSS articles or PDFs depending on the command-line argument.
- `mccayfeeds.opml` – OPML list of RSS feeds used by the fetcher.
- `danmccay.yaml` – sample configuration for the MUD agent.

## Notes

The agent code currently includes an OpenAI key placeholder. Replace it with your own key or set the `OPENAI_API_KEY` environment variable before running.
