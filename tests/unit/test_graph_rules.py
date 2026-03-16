from bulletjournal.domain.enums import NodeKind
from bulletjournal.domain.errors import GraphValidationError
from bulletjournal.domain.graph_rules import validate_acyclic, validate_unique_target_ports
from bulletjournal.domain.models import Edge, Node


def test_graph_cycle_detection() -> None:
    nodes = [
        Node(id='a', kind=NodeKind.NOTEBOOK, title='A'),
        Node(id='b', kind=NodeKind.NOTEBOOK, title='B'),
    ]
    edges = [
        Edge(id='a.out__b.in', source_node='a', source_port='out', target_node='b', target_port='in'),
        Edge(id='b.out__a.in', source_node='b', source_port='out', target_node='a', target_port='in'),
    ]

    try:
        validate_acyclic(nodes, edges)
    except GraphValidationError:
        pass
    else:
        raise AssertionError('Expected cycle validation failure')


def test_unique_target_ports() -> None:
    edges = [
        Edge(id='a.out__c.in', source_node='a', source_port='out', target_node='c', target_port='in'),
        Edge(id='b.out__c.in', source_node='b', source_port='out', target_node='c', target_port='in'),
    ]

    try:
        validate_unique_target_ports(edges)
    except GraphValidationError:
        pass
    else:
        raise AssertionError('Expected duplicate target port validation failure')
