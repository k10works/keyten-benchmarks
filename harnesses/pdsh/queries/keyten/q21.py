from typing import Any

import keyten as kt

from queries.keyten import utils

Q_NUM = 21


def q(**kwargs: Any) -> Any:

    lineitem = utils.get_line_item_ds()
    nation = utils.get_nation_ds()
    orders = utils.get_orders_ds()
    supplier = utils.get_supplier_ds()
    # Our idiomatic translation of the EXISTS pair (the reference SQL
    # optimizers decorrelate to the same shape): selective filters
    # FIRST, then the exists checks as semi joins against the small
    # candidate-order domain. Order-wide aggregates run over the lines
    # of candidate orders only, never over the whole table; the
    # count-based exists semantics match the validated translation
    # (n_all > 1 = another supplier exists; n_late == 1 = no OTHER
    # supplier delivered late, the candidate being late itself).
    saudi = supplier.inner_join(nation, [("s_nationkey", "n_nationkey")]).filter(
        kt.col("n_name") == kt.lit("SAUDI ARABIA")
    )
    cand = (
        lineitem.filter(kt.col("l_receiptdate") > kt.col("l_commitdate"))
        .inner_join(saudi, [("l_suppkey", "s_suppkey")])
        .inner_join(orders.filter(kt.col("o_orderstatus") == kt.lit("F")), [("l_orderkey", "o_orderkey")])
        .select([kt.col("l_orderkey"), kt.col("s_name")])
        .collect()
        .lazy()
    )
    ords = cand.group_by(kt.col("l_orderkey")).agg([])
    lord = (
        lineitem.semi_join(ords, [("l_orderkey", "l_orderkey")])
        .select([
            kt.col("l_orderkey"),
            (kt.col("l_receiptdate") > kt.col("l_commitdate")).alias("late"),
        ])
        .collect()
        .lazy()
    )
    keys_multi = (
        lord.group_by(kt.col("l_orderkey"))
        .agg(kt.lit(1).count().alias("n_all"))
        .filter(kt.col("n_all") > kt.lit(1))
    )
    keys_lone_late = (
        lord.filter(kt.col("late"))
        .group_by(kt.col("l_orderkey"))
        .agg(kt.lit(1).count().alias("n_late"))
        .filter(kt.col("n_late") == kt.lit(1))
    )
    return (
        cand.semi_join(keys_multi, [("l_orderkey", "l_orderkey")])
        .semi_join(keys_lone_late, [("l_orderkey", "l_orderkey")])
        .group_by(kt.col("s_name"))
        .agg(kt.lit(1).count().alias("numwait"))
        .sort(["numwait", "s_name"], descending=[True, False])
        .limit(100)
    )


if __name__ == "__main__":
    utils.run_query(Q_NUM, lambda: q().collect())
