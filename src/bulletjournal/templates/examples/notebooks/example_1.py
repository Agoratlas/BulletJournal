import marimo

__generated_with = '0.20.4'
app = marimo.App(width='medium', app_title='Example 1')

with app.setup:
    from bulletjournal.runtime import artifacts


@app.cell
def _(mo):
    mo.md("""
    # Example 1

    Load the iris dataset from a CSV file input block, standardize the column names, and publish the cleaned dataframe.
    """)
    return


@app.cell
def _():
    iris_csv = artifacts.pull_file(
        name='iris_csv',
        description='Connect a CSV file input block containing the iris dataset.',
    )
    return iris_csv


@app.cell
def _(iris_csv, pd):
    iris_dataframe = pd.read_csv(iris_csv)

    alias_map = {
        'sepallength': 'sepal_length',
        'sepallengthcm': 'sepal_length',
        'sepalwidth': 'sepal_width',
        'sepalwidthcm': 'sepal_width',
        'petallength': 'petal_length',
        'petallengthcm': 'petal_length',
        'petalwidth': 'petal_width',
        'petalwidthcm': 'petal_width',
        'species': 'species',
    }
    standardized_columns = {}
    for column in iris_dataframe.columns:
        condensed = ''.join(character for character in str(column).lower() if character.isalnum())
        standardized_columns[column] = alias_map.get(condensed, str(column).strip().lower().replace(' ', '_'))
    iris_dataframe = iris_dataframe.rename(columns=standardized_columns)

    required_columns = {'sepal_length', 'sepal_width', 'petal_length', 'petal_width', 'species'}
    missing_columns = sorted(required_columns - set(iris_dataframe.columns))
    if missing_columns:
        raise ValueError(f'Missing required iris columns: {missing_columns}')

    numeric_columns = ['sepal_length', 'sepal_width', 'petal_length', 'petal_width']
    iris_dataframe[numeric_columns] = iris_dataframe[numeric_columns].astype(float)
    iris_dataframe['species'] = iris_dataframe['species'].astype(str)
    iris_rows_count = int(iris_dataframe.shape[0])

    artifacts.push(
        iris_dataframe,
        name='iris_dataframe',
        data_type=pd.DataFrame,
        description='Clean iris dataframe loaded from the CSV input block.',
    )
    artifacts.push(
        iris_rows_count,
        name='iris_rows_count',
        data_type=int,
        description='Number of rows loaded from the iris CSV file.',
    )
    return iris_dataframe, iris_rows_count


if __name__ == '__main__':
    from bulletjournal.runtime.standalone import run_notebook_app

    run_notebook_app(app, __file__)
