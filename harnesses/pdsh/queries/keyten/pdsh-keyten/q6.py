from typing import Any

import keyten as kt

from queries.keyten import utils

Q_NUM = 6


def q(**kwargs: Any) -> Any:

    lineitem = utils.get_line_item_ds()
    var1 = utils.date(1994, 1, 1)
    var2 = utils.date(1995, 1, 1)
    return (
        lineitem.filter((kt.col("l_shipdate") >= var1) & (kt.col("l_shipdate") < var2))
        .filter((kt.col("l_discount") >= kt.lit(0.05)) & (kt.col("l_discount") <= kt.lit(0.07)))
        .filter(kt.col("l_quantity") < kt.lit(24))
        .with_columns((kt.col("l_extendedprice") * kt.col("l_discount")).alias("revenue"))
        .select(kt.col("revenue").sum().alias("revenue"))
    )


if __name__ == "__main__":
    utils.run_query(Q_NUM, lambda: q().collect())
