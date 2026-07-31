from typing import Any

import keyten as kt

from queries.keyten import utils

Q_NUM = 1


def q(**kwargs: Any) -> Any:

    lineitem = utils.get_line_item_ds()
    var1 = utils.date(1998, 9, 2)
    disc_price = kt.col("l_extendedprice") * (kt.lit(1.0) - kt.col("l_discount"))
    return (
        lineitem.filter(kt.col("l_shipdate") <= var1)
        .group_by([kt.col("l_returnflag"), kt.col("l_linestatus")])
        .agg([
            kt.col("l_quantity").sum().alias("sum_qty"),
            kt.col("l_extendedprice").sum().alias("sum_base_price"),
            disc_price.sum().alias("sum_disc_price"),
            (disc_price * (kt.lit(1.0) + kt.col("l_tax"))).sum().alias("sum_charge"),
            kt.col("l_quantity").mean().alias("avg_qty"),
            kt.col("l_extendedprice").mean().alias("avg_price"),
            kt.col("l_discount").mean().alias("avg_disc"),
            kt.lit(1).count().alias("count_order"),
        ])
        .sort(["l_returnflag", "l_linestatus"])
    )


if __name__ == "__main__":
    utils.run_query(Q_NUM, lambda: q().collect())
