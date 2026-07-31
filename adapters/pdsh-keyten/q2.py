from typing import Any

import keyten as kt

from queries.keyten import utils

Q_NUM = 2


def q(**kwargs: Any) -> Any:

    nation = utils.get_nation_ds()
    part = utils.get_part_ds()
    partsupp = utils.get_part_supp_ds()
    region = utils.get_region_ds()
    supplier = utils.get_supplier_ds()
    # q1 is referenced twice below (the min-cost group and the rejoin);
    # materialize it once, pruned to the columns both uses need -- the
    # same single evaluation the reference engine's common-subplan
    # elimination performs.
    q1 = (
        part.inner_join(partsupp, [("p_partkey", "ps_partkey")])
        .inner_join(supplier, [("ps_suppkey", "s_suppkey")])
        .inner_join(nation, [("s_nationkey", "n_nationkey")])
        .inner_join(region, [("n_regionkey", "r_regionkey")])
        .filter(kt.col("p_size") == kt.lit(15))
        .filter(utils.matches(kt.col("p_type"), "BRASS$"))
        .filter(kt.col("r_name") == kt.lit("EUROPE"))
        .select([
            kt.col("p_partkey"), kt.col("ps_supplycost"), kt.col("s_acctbal"),
            kt.col("s_name"), kt.col("n_name"), kt.col("p_mfgr"),
            kt.col("s_address"), kt.col("s_phone"), kt.col("s_comment"),
        ])
        .collect()
        .lazy()
    )
    best = q1.group_by(kt.col("p_partkey")).agg(kt.col("ps_supplycost").min().alias("ps_supplycost"))
    return (
        best.inner_join(q1, [("p_partkey", "p_partkey"), ("ps_supplycost", "ps_supplycost")])
        .select(["s_acctbal", "s_name", "n_name", "p_partkey", "p_mfgr", "s_address", "s_phone", "s_comment"])
        .sort(["s_acctbal", "n_name", "s_name", "p_partkey"], descending=[True, False, False, False])
        .limit(100)
    )


if __name__ == "__main__":
    utils.run_query(Q_NUM, lambda: q().collect())
