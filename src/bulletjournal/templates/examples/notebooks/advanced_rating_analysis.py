import marimo

__generated_with = '0.23.8'
app = marimo.App(width='medium', app_title='advanced_rating_analysis')

with app.setup:
    import marimo as mo
    import pandas as pd

    from bulletjournal.runtime import artifacts, assets


@app.cell
def _():
    mo.md(
        """
    # Advanced rating analysis

    This notebook downloads a ratings dataset and identifies movies with the
    largest rating gap between male and female audiences.
    """
    )
    return


@app.cell
def _():
    ratings_csv_url = artifacts.pull(
        name='ratings_url',
        data_type=str,
        description='URL pointing to the ratings dataset in CSV format.',
    )
    return (ratings_csv_url,)


@app.cell
def _():
    movies_df = artifacts.pull(
        name='movies',
        data_type=pd.DataFrame,
        description='Movie dataset.',
    )
    return (movies_df,)


@app.cell
def _(movies_df, ratings_csv_url):
    ratings_df = pd.read_csv(ratings_csv_url)
    ratings_df = movies_df[['imdb_title_id', 'title']].merge(ratings_df, on='imdb_title_id')
    return (ratings_df,)


@app.cell
def _(ratings_df):
    male_female_df = ratings_df[
        (ratings_df['males_allages_votes'] >= 100) & (ratings_df['females_allages_votes'] >= 100)
    ].copy()

    male_female_df['male_rating_difference'] = (
        male_female_df['males_allages_avg_vote'] - male_female_df['females_allages_avg_vote']
    )
    male_female_df['male_rating_difference_abs'] = male_female_df['male_rating_difference'].abs()
    male_female_df = male_female_df.sort_values('male_rating_difference_abs', ascending=False)

    male_female_scatter = assets.ScatterPlot(
        male_female_df.head(1000),
        x='males_allages_avg_vote',
        y='females_allages_avg_vote',
        size='male_rating_difference_abs',
        label='title',
        min_point_size=10,
        max_point_size=300,
        shape_style='filled',
        size_scaling=1.5,
        x_axis={'label_size': 20, 'label': 'Average male vote'},
        y_axis={'label_size': 20, 'label': 'Average female vote'},
    )

    assets.push(
        male_female_scatter,
        name='male_female_scatter',
        title='Male/female top 1000 rating difference',
    )
    return (male_female_df,)


@app.cell
def _(male_female_df):
    artifacts.push(
        male_female_df,
        name='ratings',
        data_type=pd.DataFrame,
        description='Ratings dataset with male/female rating differences.',
    )
    return


if __name__ == '__main__':
    from bulletjournal.runtime.standalone import run_notebook_app

    run_notebook_app(app, __file__)
