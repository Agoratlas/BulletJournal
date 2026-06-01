import marimo

__generated_with = '0.23.8'
app = marimo.App(width='medium', app_title='movie_recommendation')

with app.setup:
    import marimo as mo
    import pandas as pd

    from bulletjournal.runtime import artifacts, assets


@app.cell
def _():
    mo.md(
        """
    # Movie recommendation

    This notebook combines the processed movie and ratings datasets to surface
    high-signal recommendations, genre patterns, and top titles.
    """
    )
    return


@app.cell
def _():
    movies_df = artifacts.pull(
        name='movies',
        data_type=pd.DataFrame,
        description='Processed movie dataset.',
    )
    return (movies_df,)


@app.cell
def _():
    ratings_df = artifacts.pull(
        name='ratings',
        data_type=pd.DataFrame,
        description='Movie ratings dataset.',
    )
    return (ratings_df,)


@app.cell
def _(movies_df):
    best_movie_per_year = (
        movies_df[movies_df['votes'] >= 100_000]
        .sort_values(by=['avg_vote', 'votes'], ascending=[False, False])
        .drop_duplicates(subset=['year'])
        .sort_values(by='year', ascending=False)
    )
    return (best_movie_per_year,)


@app.cell
def _(best_movie_per_year):
    best_movie_genres = assets.PieChart(best_movie_per_year, category='genre_single', label_threshold=3)

    assets.push(
        best_movie_genres,
        name='best_movie_genres',
        title='Genre of the best movie of the year',
    )
    return


@app.cell
def _(best_movie_per_year):
    best_movie_directors = assets.PieChart(best_movie_per_year, category='director', label_threshold=3)

    assets.push(
        best_movie_directors,
        name='best_movie_directors',
        title='Director of the best movie of the year',
    )
    return


@app.cell
def _(movies_df, ratings_df):
    movies_with_rating = movies_df.merge(ratings_df, on='imdb_title_id')
    movies_with_rating = movies_with_rating[movies_with_rating['duration'] > 180]

    gender_difference_by_genre = assets.BarChart(
        movies_with_rating,
        category='genre_single',
        value='male_rating_difference',
        aggregation='average',
        category_order='value_asc',
    )

    assets.push(
        gender_difference_by_genre,
        name='gender_difference_by_genre',
        title='Male/female rating difference by genre (movies >3h)',
    )
    return


@app.cell
def _(movies_df):
    imdb_asset_collection = assets.Collection(display_mode='single')

    best_movie_per_genre = (
        movies_df[movies_df['votes'] >= 100_000]
        .sort_values(by=['avg_vote', 'votes'], ascending=[False, False])
        .drop_duplicates(subset=['genre_single'])
        .sort_values('genre_single')
    )

    for _, row in best_movie_per_genre.iterrows():
        iframe_asset = assets.Iframe(f'https://hub.toolforge.org/P345:{row["imdb_title_id"]}?site=enwiki')
        imdb_asset_collection.add_asset(iframe_asset, title=f'Best {row["genre_single"].lower()} movie')

    assets.push(imdb_asset_collection, name='imdb_top_movies', title='IMDb page for the top movie per genre')
    return


if __name__ == '__main__':
    from bulletjournal.runtime.standalone import run_notebook_app

    run_notebook_app(app, __file__)
