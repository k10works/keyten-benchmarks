from typing import Any

import keyten as kt

from queries.keyten import utils

Q_NUM = 8


def q(**kwargs: Any) -> Any:

    customer = utils.get_customer_ds()
    lineitem = utils.get_line_item_ds()
    nation = utils.get_nation_ds()
    orders = utils.get_orders_ds()
    part = utils.get_part_ds()
    region = utils.get_region_ds()
    supplier = utils.get_supplier_ds()
    var4 = utils.date(1995, 1, 1)
    var5 = utils.date(1996, 12, 31)
    n1 = nation.select([kt.col("n_nationkey"), kt.col("n_regionkey")])
    n2 = nation.select([kt.col("n_nationkey"), kt.col("n_name")])
    return (
        part.inner_join(lineitem, [("p_partkey", "l_partkey")])
        .inner_join(supplier, [("l_suppkey", "s_suppkey")])
        .inner_join(orders, [("l_orderkey", "o_orderkey")])
        .inner_join(customer, [("o_custkey", "c_custkey")])
        .inner_join(n1, [("c_nationkey", "n_nationkey")])
        .inner_join(region, [("n_regionkey", "r_regionkey")])
        .filter(kt.col("r_name") == kt.lit("AMERICA"))
        .inner_join(n2, [("s_nationkey", "n_nationkey")])
        .filter((kt.col("o_orderdate") >= var4) & (kt.col("o_orderdate") <= var5))
        .filter(kt.col("p_type") == kt.lit("ECONOMY ANODIZED STEEL"))
        .select([
            kt.col("o_orderdate").year().alias("o_year"),
            (kt.col("l_extendedprice") * (kt.lit(1.0) - kt.col("l_discount"))).alias("volume"),
            kt.col("n_name").alias("nation"),
        ])
        .with_columns(
            kt.if_else(kt.col("nation") == kt.lit("BRAZIL"), kt.col("volume"), kt.lit(0.0)).alias("_tmp")
        )
        .group_by(kt.col("o_year"))
        .agg([kt.col("_tmp").sum().alias("_t"), kt.col("volume").sum().alias("_v")])
        .with_columns((kt.col("_t") / kt.col("_v")).alias("mkt_share"))
        .select([kt.col("o_year"), kt.col("mkt_share")])
        .sort("o_year")
    )


if __name__ == "__main__":
    utils.run_query(Q_NUM, lambda: q().collect())
