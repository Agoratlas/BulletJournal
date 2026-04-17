import marimo

__generated_with = '0.20.4'
app = marimo.App(width='medium', app_title='Example 2')

with app.setup:
    from bulletjournal.runtime import artifacts


@app.cell
def _(mo):
    mo.md("""
    # Example 2

    Derive a categorical size label from petal length and append it to the iris dataset.
    """)
    return


@app.cell
def _(pd):
    iris_dataframe = artifacts.pull(
        name='iris_dataframe',
        data_type=pd.DataFrame,
        description='Connect Example 1 iris_dataframe.',
    )
    return iris_dataframe


@app.cell
def _(iris_dataframe, pd):
    def classify_size(petal_length: float) -> str:
        if petal_length < 2.0:
            return 'S'
        if petal_length < 4.5:
            return 'M'
        if petal_length < 5.5:
            return 'L'
        return 'XL'

    iris_with_size_category = iris_dataframe.copy()
    iris_with_size_category['size_category'] = iris_with_size_category['petal_length'].apply(classify_size)

    artifacts.push(
        iris_with_size_category,
        name='iris_with_size_category',
        data_type=pd.DataFrame,
        description='Iris dataframe enriched with a petal-length size category.',
    )
    return iris_with_size_category


if __name__ == '__main__':
    from bulletjournal.runtime.standalone import run_notebook_app

    run_notebook_app(app, __file__)
