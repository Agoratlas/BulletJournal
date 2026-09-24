import marimo

__generated_with = '0.23.8'
app = marimo.App(width='medium', app_title='movie_dataset_download')

with app.setup:
    import time

    import marimo as mo
    import pandas as pd

    from bulletjournal.runtime import artifacts, assets


@app.cell
def _():
    mo.md(
        """
    # Download movie dataset

    This notebook downloads a CSV file from the provided URL and publishes it as a reusable movie dataset artifact.
    """
    )
    return


@app.cell
def _():
    movie_csv_url = artifacts.pull(
        name='url',
        data_type=str,
        description='URL pointing to the movie dataset in CSV format.',
    )
    return (movie_csv_url,)


@app.cell
def _(movie_csv_url):
    t0 = time.perf_counter()
    movie_df = pd.read_csv(movie_csv_url)
    t1 = time.perf_counter()
    return movie_df, t0, t1


@app.cell
def _(movie_df):
    artifacts.push(
        movie_df,
        name='movies',
        data_type=pd.DataFrame,
        description='Movie dataset loaded from the provided URL.',
    )
    return


@app.cell
def _(movie_csv_url, movie_df, t0, t1):
    memory_usage_mb = movie_df.memory_usage(deep=True).sum() / 1_000_000
    non_empty_values = int(movie_df.count().sum())
    empty_values = movie_df.size - non_empty_values
    md_summary = assets.Markdown(
        f"""## Download
- **Source:** [Dataset URL]({movie_csv_url})
  - Loaded into a `pandas.DataFrame` and published downstream as the `movies` artifact
- **Status:** <span style="color:#3aa85b">**Download complete**</span>
- **Elapsed time:** {t1 - t0:.2f} seconds

---

## Dataset at a glance

| Metric | Value |
| --- | ---: |
| Rows | {movie_df.shape[0]} |
| Columns | {movie_df.shape[1]} |
| DataFrame memory usage | {memory_usage_mb:.1f} MB |
| Non-empty values | {non_empty_values:,} |
| Empty values | {empty_values:,} |
"""
    )

    assets.push(md_summary, name='dataset_summary', title='Movie dataset summary')
    return


@app.cell
def _(movie_df):
    assets.push(assets.DataFrame(movie_df), name='movie_dataset', title='Movie dataset')
    return


if __name__ == '__main__':
    from bulletjournal.runtime.standalone import run_notebook_app

    run_notebook_app(app, __file__)
