import marimo

__generated_with = '0.20.4'
app = marimo.App(width='medium', app_title='Example 3')

with app.setup:
    import marimo as mo
    import pandas as pd

    from bulletjournal.runtime import artifacts


@app.cell
def _(mo):
    mo.md("""
    # Example 3

    Compute a few simple validation and descriptive statistics from the enriched iris dataset.
    """)
    return


@app.cell
def _(pd):
    expected_rows_count = artifacts.pull(
        name='expected_rows_count',
        data_type=int,
        description='Connect Example 1 iris_rows_count.',
    )
    iris_with_size_category = artifacts.pull(
        name='iris_with_size_category',
        data_type=pd.DataFrame,
        description='Connect Example 2 iris_with_size_category.',
    )
    return expected_rows_count, iris_with_size_category


@app.cell
def _(expected_rows_count, iris_with_size_category):
    avg_petal_length = float(iris_with_size_category['petal_length'].mean())
    ordered_labels = ['S', 'M', 'L', 'XL']
    observed_counts = iris_with_size_category['size_category'].value_counts().to_dict()
    size_category_counts = {label: int(observed_counts.get(label, 0)) for label in ordered_labels}
    row_count_ok = int(iris_with_size_category.shape[0]) == expected_rows_count

    artifacts.push(
        avg_petal_length,
        name='avg_petal_length',
        data_type=float,
        description='Average petal length across the enriched iris dataset.',
    )
    artifacts.push(
        size_category_counts,
        name='size_category_counts',
        data_type=dict,
        description='Counts of rows in each derived size category.',
    )
    artifacts.push(
        row_count_ok,
        name='row_count_ok',
        data_type=bool,
        description='Whether the transformed dataset kept the expected number of rows.',
    )
    return avg_petal_length, row_count_ok, size_category_counts


if __name__ == '__main__':
    from bulletjournal.runtime.standalone import run_notebook_app

    run_notebook_app(app, __file__)
