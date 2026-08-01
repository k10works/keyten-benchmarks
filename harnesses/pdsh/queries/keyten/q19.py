from typing import Any

import keyten as kt

from queries.keyten import utils

Q_NUM = 19


def q(**kwargs: Any) -> Any:

    lineitem = utils.get_line_item_ds()
    part = utils.get_part_ds()
    def between(c, lo, hi):
        return (kt.col(c) >= kt.lit(lo)) & (kt.col(c) <= kt.lit(hi))
    return (
        part.inner_join(lineitem, [("p_partkey", "l_partkey")])
        .filter(kt.col("l_shipmode").is_in(["AIR", "AIR REG"]))
        .filter(kt.col("l_shipinstruct") == kt.lit("DELIVER IN PERSON"))
        .filter(
            (
                (kt.col("p_brand") == kt.lit("Brand#12"))
                & kt.col("p_container").is_in(["SM CASE", "SM BOX", "SM PACK", "SM PKG"])
                & between("l_quantity", 1, 11)
                & between("p_size", 1, 5)
            )
            | (
                (kt.col("p_brand") == kt.lit("Brand#23"))
                & kt.col("p_container").is_in(["MED BAG", "MED BOX", "MED PKG", "MED PACK"])
                & between("l_quantity", 10, 20)
                & between("p_size", 1, 10)
            )
            | (
                (kt.col("p_brand") == kt.lit("Brand#34"))
                & kt.col("p_container").is_in(["LG CASE", "LG BOX", "LG PACK", "LG PKG"])
                & between("l_quantity", 20, 30)
                & between("p_size", 1, 15)
            )
        )
        .select((kt.col("l_extendedprice") * (kt.lit(1.0) - kt.col("l_discount"))).sum().alias("revenue"))
    )


if __name__ == "__main__":
    utils.run_query(Q_NUM, lambda: q().collect())
