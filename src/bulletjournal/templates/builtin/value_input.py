import marimo

__generated_with = '0.20.4'
app = marimo.App(width='medium', app_title='Value input')

with app.setup:
    from bulletjournal.runtime import artifacts


@app.cell
def _():
    value = 42
    artifacts.push(value, name='value', data_type=int, is_output=True, description='Constant value output')
    return value


if __name__ == '__main__':
    from bulletjournal.runtime.standalone import run_notebook_app

    run_notebook_app(app, __file__)
