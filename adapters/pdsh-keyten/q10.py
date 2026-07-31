from typing import Any

import keyten as kt

from queries.keyten import utils

Q_NUM = 10


def q(**kwargs: Any) -> Any:

    customer = utils.get_customer_ds()
    lineitem = utils.get_line_item_ds()
    nation = utils.get_nation_ds()
    orders = utils.get_orders_ds()
    var1 = utils.date(1993, 10, 1)
    var2 = utils.date(1994, 1, 1)
    return (
        customer.inner_join(orders, [("c_custkey", "o_custkey")])
        .inner_join(lineitem, [("o_orderkey", "l_orderkey")])
        .inner_join(nation, [("c_nationkey", "n_nationkey")])
        .filter((kt.col("o_orderdate") >= var1) & (kt.col("o_orderdate") < var2))
        .filter(kt.col("l_returnflag") == kt.lit("R"))
        .group_by([
            kt.col("c_custkey"), kt.col("c_name"), kt.col("c_acctbal"), kt.col("c_phone"),
            kt.col("n_name"), kt.col("c_address"), kt.col("c_comment"),
        ])
        .agg((kt.col("l_extendedprice") * (kt.lit(1.0) - kt.col("l_discount"))).sum().alias("revenue"))
        .select([
            kt.col("c_custkey"), kt.col("c_name"), kt.col("revenue"), kt.col("c_acctbal"),
            kt.col("n_name"), kt.col("c_address"), kt.col("c_phone"), kt.col("c_comment"),
        ])
        .sort("revenue", descending=True)
        .limit(20)
    )


if __name__ == "__main__":
    utils.run_query(Q_NUM, lambda: q().collect())
