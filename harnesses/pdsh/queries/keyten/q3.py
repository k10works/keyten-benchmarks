from typing import Any

import keyten as kt

from queries.keyten import utils

Q_NUM = 3


def q(**kwargs: Any) -> Any:

    customer = utils.get_customer_ds()
    lineitem = utils.get_line_item_ds()
    orders = utils.get_orders_ds()
    var2 = utils.date(1995, 3, 15)
    return (
        customer.filter(kt.col("c_mktsegment") == kt.lit("BUILDING"))
        .inner_join(orders, [("c_custkey", "o_custkey")])
        .inner_join(lineitem, [("o_orderkey", "l_orderkey")])
        .filter(kt.col("o_orderdate") < var2)
        .filter(kt.col("l_shipdate") > var2)
        .with_columns((kt.col("l_extendedprice") * (kt.lit(1.0) - kt.col("l_discount"))).alias("revenue"))
        .group_by([kt.col("o_orderkey"), kt.col("o_orderdate"), kt.col("o_shippriority")])
        .agg(kt.col("revenue").sum().alias("revenue"))
        .select([
            kt.col("o_orderkey").alias("l_orderkey"),
            kt.col("revenue"),
            kt.col("o_orderdate"),
            kt.col("o_shippriority"),
        ])
        .sort(["revenue", "o_orderdate"], descending=[True, False])
        .limit(10)
    )


if __name__ == "__main__":
    utils.run_query(Q_NUM, lambda: q().collect())
