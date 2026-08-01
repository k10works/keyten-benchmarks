from typing import Any

import keyten as kt

from queries.keyten import utils

Q_NUM = 9


def q(**kwargs: Any) -> Any:

    lineitem = utils.get_line_item_ds()
    nation = utils.get_nation_ds()
    orders = utils.get_orders_ds()
    part = utils.get_part_ds()
    partsupp = utils.get_part_supp_ds()
    supplier = utils.get_supplier_ds()
    return (
        part.inner_join(partsupp, [("p_partkey", "ps_partkey")])
        .inner_join(supplier, [("ps_suppkey", "s_suppkey")])
        .inner_join(lineitem, [("p_partkey", "l_partkey"), ("ps_suppkey", "l_suppkey")])
        .inner_join(orders, [("l_orderkey", "o_orderkey")])
        .inner_join(nation, [("s_nationkey", "n_nationkey")])
        .filter(kt.col("p_name").str_contains("green"))
        .select([
            kt.col("n_name").alias("nation"),
            kt.col("o_orderdate").year().alias("o_year"),
            (
                kt.col("l_extendedprice") * (kt.lit(1.0) - kt.col("l_discount"))
                - kt.col("ps_supplycost") * kt.col("l_quantity")
            ).alias("amount"),
        ])
        .group_by([kt.col("nation"), kt.col("o_year")])
        .agg(kt.col("amount").sum().alias("sum_profit"))
        .sort(["nation", "o_year"], descending=[False, True])
    )


if __name__ == "__main__":
    utils.run_query(Q_NUM, lambda: q().collect())
