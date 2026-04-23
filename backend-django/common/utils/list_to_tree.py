# Author 臧成龙
# coding=utf-8
# @Time    : 2022/5/16 21:09
# @File    : list_to_tree.py
# @Software: PyCharm
# @qq: 939589097


def add_node(p, node):
    # ⼦节点list
    p["children"] = []
    for n in node:
        if n.get("parent_id") == p.get("id"):
            p["children"].append(n)
    # 递归⼦节点，查找⼦节点的节点
    for t in p["children"]:
        if not t.get("children"):
            t["children"] = []
        t["children"].append(add_node(t, node))
    # 退出递归的条件
    if len(p["children"]) == 0:
        p.pop('children')
        p["choice"] = 1
        return


def list_to_route(data):
    root = []
    node = []
    # 初始化数据，获取根节点和其他子节点list
    for d in data:
        d['meta'] = {
            'title': d.pop('title'),
            'ignoreKeepAlive': d.pop('keep_alive'),
            'orderNo': d.pop('sort'),
            'hideMenu': d.pop('hide_menu'),
            'icon': d.pop('icon')
        }
        if d.get("type") == 2:
            d["meta"]["frameSrc"] = d.pop("frame_src")

        d["choice"] = 0
        if d.get("parent_id") is None:
            root.append(d)
        else:
            node.append(d)
    # print("root----",root)
    # print("node----",node)
    # 查找子节点
    for p in root:
        add_node(p, node)
    # 无子节点
    if len(root) == 0:
        return node

    return root


def list_to_tree(data):
    root = []
    node = []
    # 初始化数据，获取根节点和其他子节点list

    for d in data:
        d["choice"] = 0
        if d.get("parent_id") is None:
            root.append(d)
        else:
            node.append(d)
    # print("root----",root)
    # print("node----",node)
    # 查找子节点
    for p in root:
        add_node(p, node)
    # 无子节点
    if len(root) == 0:
        return node

    return root


def list_to_route_v5(menus: list) -> list:
    """
    从菜单列表构建菜单树（已读取所有菜单数据的情况）
    """

    # 创建 ID 到菜单数据的映射
    menu_map = {menu['id']: menu for menu in menus}

    # 构建树结构
    root_menus = []

    for menu in menus:
        pid = menu['parent_id']

        # 添加 meta 字段
        meta = {}
        for field in [
            'activeIcon', 'activePath', 'affixTab', 'affixTabOrder', 'badge',
            'badgeType', 'badgeVariants', 'hideChildrenInMenu', 'hideInBreadcrumb',
            'hideInMenu', 'hideInTab', 'icon', 'iframeSrc', 'keepAlive',
            'link', 'maxNumOfOpenTab', 'noBasicLayout', 'openInNewWindow',
            'order', 'query', 'title'
        ]:
            if field in menu and menu[field] is not None:
                meta[field] = menu[field]

        if meta:
            menu['meta'] = meta

        # 移除不需要的字段（保留 parent_id 用于前端父菜单选择器）
        for key in list(menu.keys()):
            if key.endswith('_id') and key not in ('id', 'parentId', 'parent_id'):
                menu.pop(key)

        # 找到父节点
        if pid in menu_map:
            parent = menu_map[pid]
            if 'children' not in parent:
                parent['children'] = []
            parent['children'].append(menu)
        else:
            # 根节点
            root_menus.append(menu)

    # 对子菜单排序
    def sort_children(node):
        if 'children' in node:
            node['children'].sort(key=lambda x: x.get('order', 0))
            for child in node['children']:
                sort_children(child)

    for root in root_menus:
        sort_children(root)

    return root_menus
