"""Allocate an integer quota across weighted recipients."""


def allocate_quota(total, weights):
    weight_total = sum(weights)
    return [round(total * weight / weight_total) for weight in weights]
