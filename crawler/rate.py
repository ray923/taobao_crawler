import json
import time

import gevent
from gevent import queue

from .model import Item, Rate
from .utils import *


class RateCrawler:
    """
    从数据库中寻取未爬取评论的商品，爬取其所有评论，并插入至数据库中。
    插入数据示例：{
    "buyCount" : 0,
    "useful" : true,
    "item_id" : "560697135358",
    "rate_id" : NumberLong("331495062062"),
    "rateDate" : "2017-11-23 23:16:40",
    "rate_content" : "挺棒的手机 快递也快非常满意",
    "auctionSku" : "机身颜色:香槟色;套餐类型:官方标配;存储容量:64GB;版本类型:中国大陆",
    "anony" : true,
    "size_info" : "机身颜色:香槟色;套餐类型:官方标配;存储容量:64GB;版本类型:中国大陆"
    }
    """

    def __init__(self, db, timeout=3):
        """
        初始化 RateCrawler 实例

        :param db: 一个 pymongo.MongoClient.db 的实例
        :param timeout: 爬取超时时间, 默认值为 3
        """
        self.__db = db
        self.__collection = self.__db.rates
        self.__collection.ensure_index('rate_id', unique=True)
        self.timeout = timeout

    def run(self):
        """
        运行商品评论爬虫，插入至数据库中。
        """
        self.__items = self.__db.items.find({'is_crawled': False})
        items = []
        # 先把数据读到内存
        for item in self.__items:
            items.append(
                Item(item['item_id'], item['seller_id'], item['title'], item['area'], item['location'], item['price'],
                     item['sellerLoc'], item['sold'], False))
            pass

        for item in items:
            base_url = "https://rate.tmall.com/list_detail_rate.htm?itemId={}&sellerId={}&currentPage={}&pageSize=1000000"
            url = base_url.format(item.item_id, item.seller_id, 1)
            try:
                # 这里返回的数据不是纯json，需要在两边加上{}
                print(url)
                body = "{" + get_body(url, self.timeout) + "}"
                if len(body) == 2:
                    add_failed_url(self.__db, url)
                    continue
            except Exception as e:
                print(e)
                add_failed_url(self.__db, url)
                continue

            # 获取评论页数
            page_num = self.__parse_page_num(body)
            print(item.title, ' ', item.item_id, '--------->', page_num,
                  time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time())))

            # 使用gevent并发爬取，把数据存在queue里
            tasks = []
            q = gevent.queue.Queue()
            for i in range(1, page_num + 1):
                url = base_url.format(item.item_id, item.seller_id, i)
                tasks.append(gevent.spawn(self.__async_get_rates, url, q))
            gevent.joinall(tasks)
            print("adding data of item:{}".format(item.item_id),
                  time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time())))
            # 逐个添加到数据库
            while not q.empty():
                body = q.get()
                if len(body) == 2:
                    add_failed_url(self.__db, url)
                    continue
                rates = self.__parse_rates(body, item.item_id)
                self.__add_rates(rates)
            # 把item的is_crawled设为1
            self.__update_item(item)

    def __async_get_rates(self, url, q):
        """ 异步发送get请求 """
        try:
            body = "{" + get_body(url, self.timeout) + "}"
            q.put(body)
        except:
            add_failed_url(self.__db, url)
        print(url)

    def __parse_page_num(self, body):
        """ 解析商品的评论页数 """
        try:
            data = json.loads(body)
            page_num = data['rateDetail']['paginator']['lastPage']
            return page_num
        except:
            return 0

    def __parse_rates(self, body, item_id):
        """ 解析商品的评论 """
        rates = []
        try:
            data = json.loads(body)
        except:
            return []
        rate_list = data['rateDetail']['rateList']
        if len(rate_list) == 0:
            return []
        for _rate in rate_list:
            rate = Rate(_rate['id'], _rate['auctionSku'], _rate['rateContent'], _rate['auctionSku'], _rate['buyCount'],
                        _rate['rateDate'], _rate['useful'], _rate['anony'], item_id)
            rates.append(rate)
        return rates

    def __add_rates(self, rates):
        """ 添加商品评论 """
        for rate in rates:
            try:
                self.__collection.insert(rate.dict())
            except:
                pass

    def __update_item(self, item):
        """ 把当前商品设置为：已经爬取过 """
        self.__db.items.update({'item_id': item.item_id}, {
            '$set': {'is_crawled': True},
        })
