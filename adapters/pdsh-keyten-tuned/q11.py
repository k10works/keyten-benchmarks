from typing import Any

import keyten as kt

from queries.keyten import utils

Q_NUM = 11


def q(**kwargs: Any) -> Any:
    nation = utils.get_nation_ds()
    partsupp = utils.get_part_supp_ds()
    supplier = utils.get_supplier_ds()
    var2 = 0.0001 / utils.settings.scale_factor
    german_suppliers = supplier.inner_join(
        nation.filter(kt.col("n_name") == kt.lit("GERMANY")),
        [("s_nationkey", "n_nationkey")],
    )
    return (
        partsupp.semi_join(german_suppliers, [("ps_suppkey", "s_suppkey")])
        .group_by(kt.col("ps_partkey"))
        .agg(
            (kt.col("ps_supplycost") * kt.col("ps_availqty"))
            .sum()
            .alias("value")
        )
        .with_columns(kt.col("value").sum().over([]).alias("threshold"))
        .filter(kt.col("value") > kt.col("threshold") * kt.lit(var2))
        .with_columns(utils.round2(kt.col("value")).alias("value"))
        .select([kt.col("ps_partkey"), kt.col("value")])
        .sort("value", descending=True)
    )


if __name__ == "__main__":
    utils.run_query(Q_NUM, lambda: q().collect())
