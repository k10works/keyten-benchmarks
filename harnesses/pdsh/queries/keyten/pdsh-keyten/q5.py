from typing import Any

import keyten as kt

from queries.keyten import utils

Q_NUM = 5


def q(**kwargs: Any) -> Any:

    customer = utils.get_customer_ds()
    lineitem = utils.get_line_item_ds()
    nation = utils.get_nation_ds()
    orders = utils.get_orders_ds()
    region = utils.get_region_ds()
    supplier = utils.get_supplier_ds()
    var1 = utils.date(1994, 1, 1)
    var2 = utils.date(1995, 1, 1)
    return (
        region.filter(kt.col("r_name") == kt.lit("ASIA"))
        .inner_join(nation, [("r_regionkey", "n_regionkey")])
        .inner_join(customer, [("n_nationkey", "c_nationkey")])
        .inner_join(orders, [("c_custkey", "o_custkey")])
        .inner_join(lineitem, [("o_orderkey", "l_orderkey")])
        .inner_join(supplier, [("l_suppkey", "s_suppkey"), ("n_nationkey", "s_nationkey")])
        .filter((kt.col("o_orderdate") >= var1) & (kt.col("o_orderdate") < var2))
        .with_columns((kt.col("l_extendedprice") * (kt.lit(1.0) - kt.col("l_discount"))).alias("revenue"))
        .group_by(kt.col("n_name"))
        .agg(kt.col("revenue").sum().alias("revenue"))
        .sort("revenue", descending=True)
    )


if __name__ == "__main__":
    utils.run_query(Q_NUM, lambda: q().collect())
