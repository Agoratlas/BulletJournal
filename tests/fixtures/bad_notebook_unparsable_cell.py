import marimo

app = marimo.App()

with app.setup:
    pass


app._unparsable_cell(
    r"""
value = artifacts.pull(name='value', data_type=int, default=1)
broken =
return value
"""
)


if __name__ == '__main__':
    app.run()
