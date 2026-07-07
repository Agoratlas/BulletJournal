import marimo

__generated_with = '0.23.8'
app = marimo.App(width='medium', app_title='duration_and_date_analysis')

with app.setup:
    import marimo as mo
    import pandas as pd

    from bulletjournal.runtime import artifacts, assets


@app.cell
def _():
    mo.md(
        """
    # Movie duration and release date analysis

    This notebook analyzes the movie dataset and produces visualizations for
    runtimes, release dates, yearly trends, and long-form outliers.
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
    duration_hist = assets.Histogram(movies_df[movies_df['duration'] < 180], x='duration')

    assets.push(duration_hist, name='duration_hist', title='Distribution of movie durations (<3h)')
    return


@app.cell
def _(movies_df):
    movies_df_cleaned_date = movies_df[movies_df['date_published'].str.match(r'[0-9]{4}-[0-9]{2}-[0-9]{2}')].copy()
    movies_df_cleaned_date['date_published'] = pd.to_datetime(movies_df_cleaned_date['date_published'])
    movies_df_cleaned_date = movies_df_cleaned_date[movies_df_cleaned_date['date_published'] >= '1980-01-01']
    movies_df_cleaned_date = movies_df_cleaned_date[movies_df_cleaned_date['date_published'] < '2021-01-01']
    return (movies_df_cleaned_date,)


@app.cell
def _(movies_df_cleaned_date):
    publication_hist = assets.Histogram(movies_df_cleaned_date, x='date_published')

    assets.push(publication_hist, name='publication_hist', title='Distribution of publication date')
    return


@app.cell
def _(movies_df):
    year_duration_bars = assets.BarChart(
        movies_df,
        category='year',
        category_order='category_asc',
        value='duration',
        aggregation='mean',
    )

    assets.push(year_duration_bars, name='year_duration_bars', title='Average duration by publication year')
    return


@app.cell
def _(movies_df):
    long_movies_df = movies_df[movies_df['duration'] > 180].copy()

    top_countries = long_movies_df['country'].value_counts().nlargest(10).index
    long_movies_df['country_tag'] = long_movies_df['country'].where(
        long_movies_df['country'].isin(top_countries),
        'Other',
    )

    year_duration_scatter = assets.ScatterPlot(
        long_movies_df,
        x='year',
        y='duration',
        label='title',
        color='country_tag',
        size='avg_vote',
        size_scaling=3.0,
        y_axis={'scale': 'log'},
    )

    assets.push(year_duration_scatter, name='year_duration_scatter', title='Duration vs year (movies >3h)')
    return


if __name__ == '__main__':
    from bulletjournal.runtime.standalone import run_notebook_app

    run_notebook_app(app, __file__)
