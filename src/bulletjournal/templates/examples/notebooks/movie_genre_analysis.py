import marimo

__generated_with = '0.23.8'
app = marimo.App(width='medium', app_title='movie_genre_analysis')

with app.setup:
    import marimo as mo
    import pandas as pd

    from bulletjournal.runtime import artifacts, assets


@app.cell
def _():
    mo.md(
        """
    # Genre analysis

    This notebook simplifies multi-genre labels into a single representative
    genre so later steps can compare movies more cleanly.
    """
    )
    return


@app.cell
def _():
    movies_df = artifacts.pull(
        name='movies',
        data_type=pd.DataFrame,
        description='Movie dataset.',
    )
    return (movies_df,)


@app.cell
def _(movies_df):
    movies_df_single_genre = movies_df.copy()

    movies_df_single_genre['genre_single'] = (
        movies_df_single_genre['genre']
        .str.split(r',\s*')
        .explode()
        .sample(frac=1, random_state=123)
        .groupby(level=0)
        .first()
    )
    return (movies_df_single_genre,)


@app.cell
def _(movies_df_single_genre):
    genre_breakdown = assets.PieChart(
        movies_df_single_genre,
        category='genre_single',
        label_threshold=2,
        show_percentages=True,
    )

    assets.push(genre_breakdown, name='genre_breakdown', title='Movie genre breakdown')
    return


@app.cell
def _(movies_df_single_genre):
    genre_breakdown_yearly = assets.BarChart(
        movies_df_single_genre,
        category='year',
        group='genre_single',
        value='genre_single',
        aggregation='count',
        group_mode='stacked',
        group_normalize=True,
    )

    assets.push(
        genre_breakdown_yearly,
        name='genre_breakdown_yearly',
        title='Movie genre breakdown per year',
    )
    return


@app.cell
def _(movies_df_single_genre):
    artifacts.push(
        movies_df_single_genre,
        name='movies_single_genre',
        data_type=pd.DataFrame,
        description='Movie dataset enriched with a `genre_single` column.',
    )
    return


if __name__ == '__main__':
    from bulletjournal.runtime.standalone import run_notebook_app

    run_notebook_app(app, __file__)
