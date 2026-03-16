import marimo

__generated_with = '0.20.4'
app = marimo.App(width='medium', app_title='BulletJournal notebook')

with app.setup:
    from bulletjournal.runtime import artifacts
    import marimo as mo
    import pandas as pd


@app.cell
def _(mo):
    mo.md('''
    # BulletJournal notebook

    Generic notebook scaffold with one default input and one sample output.
    ''')
    return


@app.cell
def _():
    sample_count = artifacts.pull(name='sample_count', data_type=int, default=10)
    return sample_count


@app.cell
def _(pd, sample_count):
    frame = pd.DataFrame({'value': list(range(sample_count))})
    artifacts.push(frame, name='sample_df', data_type=pd.DataFrame, is_output=True, description='Sample output frame')
    return frame


if __name__ == '__main__':
    from bulletjournal.runtime.standalone import run_notebook_app

    run_notebook_app(app, __file__)
