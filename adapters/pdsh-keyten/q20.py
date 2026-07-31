from typing import Any

import keyten as kt

from queries.keyten import utils

Q_NUM = 20


def q(**kwargs: Any) -> Any:

    lineitem = utils.get_line_item_ds()
    nation = utils.get_nation_ds()
    part = utils.get_part_ds()
    partsupp = utils.get_part_supp_ds()
    supplier = utils.get_supplier_ds()
    var1 = utils.date(1994, 1, 1)
    var2 = utils.date(1995, 1, 1)
    q1 = (
        lineitem.filter((kt.col("l_shipdate") >= var1) & (kt.col("l_shipdate") < var2))
        .group_by([kt.col("l_partkey"), kt.col("l_suppkey")])
        .agg(kt.col("l_quantity").sum().alias("sum_quantity"))
        .with_columns((kt.col("sum_quantity") * kt.lit(0.5)).alias("sum_quantity"))
    )
    q2 = nation.filter(kt.col("n_name") == kt.lit("CANADA"))
    q3 = supplier.inner_join(q2, [("s_nationkey", "n_nationkey")])
    sel = (
        partsupp.semi_join(
            part.filter(utils.starts_with(kt.col("p_name"), "forest")),
            [("ps_partkey", "p_partkey")],
        )
        .inner_join(q1, [("ps_suppkey", "l_suppkey"), ("ps_partkey", "l_partkey")])
        .filter(kt.col("ps_availqty") > kt.col("sum_quantity"))
    )
    return (
        q3.semi_join(sel, [("s_suppkey", "ps_suppkey")])
        .select(["s_name", "s_address"])
        .sort("s_name")
    )


if __name__ == "__main__":
    utils.run_query(Q_NUM, lambda: q().collect())
