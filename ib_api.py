"""
Module to facilitate trading through Interactive Brokers's API
see: https://interactivebrokers.github.io/tws-api/index.html

Brent Maranzano
Dec. 14, 2018

Classes
    IBClient (EClient): Creates a socket to TWS or IBGateway, and handles
        sending commands to IB through the socket.
    IBWrapper (EWrapper): Hanldes the incoming data from IB. Many of these
        methods are callbacks from the request commands.
    IBApp (IBWrapper, IBClilent): This provides the main functionality. Many
        of the methods are over-rides of the IBWrapper commands to customize
        the functionality.
"""

import os.path
import time
import logging
import threading
import json
import pandas as pd
from datetime import datetime
from ibapi import wrapper
from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.common import OrderId, ListOfContractDescription, BarData,\
        HistogramDataList, TickerId
from ibapi.order import Order
from ibapi.order_state import OrderState

API_THREAD = None


def setup_logger():
    """Setup the logger.
    """
    if not os.path.exists("log"):
        os.makedirs("log")

    time.strftime("pyibapi.%Y%m%d_%H%M%S.log")

    recfmt = "(%(threadName)s) %(asctime)s.%(msecs)03d %(levelname)s" \
             "%(filename)s:%(lineno)d %(message)s"

    timefmt = '%y%m%d_%H:%M:%S'

    logging.basicConfig(
        filename=time.strftime("log/pyibapi.%y%m%d_%H%M%S.log"),
        filemode="w",
        level=logging.INFO,
        format=recfmt, datefmt=timefmt
    )
    logger = logging.getLogger()
    console = logging.StreamHandler()
    console.setLevel(logging.ERROR)
    logger.addHandler(console)
    logging.debug("now is %s", datetime.now())


class IBClient(EClient):
    """Subclass EClient, which delivers message to the TWS API socket.
    """
    def __init__(self, app_wrapper):
        EClient.__init__(self, app_wrapper)


class IBWrapper(wrapper.EWrapper):
    """Subclass EWrapper, which translates messages from the TWS API socket
    to the program.
    """
    def __init__(self):
        wrapper.EWrapper.__init__(self)


class HistoricalRequestError(Exception):
    """Exceptions generated during requesting historical stock price data.
    """
    def __init__(self, message, errors):
        super().__init__(message)

        self.errors = errors
        self.message = message


class IBApp(IBWrapper, IBClient):
    """Main program class. The TWS calls nextValidId after connection, so
    the method is over-ridden to provide an entry point into the program.

    class variables:
    saved_contracts (dict): keys are symbols, values are dictionaries of
        information to uniquely define a contract used for stock trading.
        {symbol: {'contract_info_dictionary'}}
    saved_orders (dict): keys are order ids, values are Order, Contract
        {id: {order: Order, contract: Contract}}
    TODO
    positions
    """
    def __init__(self):
        IBWrapper.__init__(self)
        IBClient.__init__(self, app_wrapper=self)

        self.order_id = None
        self.saved_contract_details = {}
        self.positions = []
        self._contract_details = {}
        self._saved_orders = {}
        self._open_orders = []
        self._historical_data = []
        self._historical_data_req_end = False
        self._histogram = None
        self._load_contracts('contract_file.json')

    def error(self, reqId: TickerId, errorCode: int, errorString: str):
        """Overide EWrapper error method.
        """
        super().error(reqId, errorCode, errorString)
        print(reqId)

    def _load_contracts(self, filename):
        """Load saved contracts.
        """
        try:
            with open(filename, mode='r') as file_obj:
                self.saved_contracts = json.load(file_obj)
        except FileNotFoundError:
            pass

    def _save_contracts(self):
        """Save contracts.
        """
        with open("contracts.json", mode='a') as file_obj:
            json.dump(self._contract_details, file_obj)

    def nextValidId(self, orderId: int):
        """Method of EWrapper.
        Sets the order_id class variable.
        This method is called from after connection completion, so
        provides an entry point into the class.
        """
        super().nextValidId(orderId)
        self.order_id = orderId
        return self

    def _get_next_order_id(self):
        """Retrieve the current class variable order_id and increment
        it by one.

        Returns (int) current order_id
        """
        #  reqIds can be used to update the order_id, if tracking is lost.
        # self.reqIds(-1)
        current_order_id = self.order_id
        self.order_id += 1
        return current_order_id

    def get_contract_details(self, symbol=None):
        """Find the contract (STK, USD, NYSE|NASDAY.NMS|ARGA) for the symbol.
        Upon execution of IB backend, the EWrapper.symbolSamples is called,
        which is over-ridden to save the contracts to a class dictionary.
        This function then monitors the class dictionary until
        the symbol is found and then returns the contract.

        Arguments:
            symbol (string): Ticker.

        Returns: (Contract) Contract for the symbol.
        """
        # If the symbol has not already been saved, look it up.
        if symbol not in self.saved_contract_details:
            self._contract_details = None
            # The IB server will call symbolSamples upon completion.
            self.reqMatchingSymbols(1001, symbol)

            # Loop until the server has completed the request.
            while self._contract_details is None:
                time.sleep(0.2)

            # Select the proper contract
            for contract in self._contract_details:
                if contract.symbol == symbol and contract.currency == "USD"\
                        and contract.secType == "STK":
                    # NYSE stock
                    if contract.primaryExchange == "NYSE":
                        break
                    # Common ETFs
                    elif contract.primaryExchange == "ARCA":
                        break
                    # Nasdaq stock
                    elif contract.primaryExchange == "NASDAQ.NMS":
                        contract.primaryExchange = "ISLAND"
                        break

            # Save the contract information needed for defining a Contract.
            self.saved_contract_details[symbol] = {
                'currency': contract.currency,
                'secType': contract.secType,
                'exchange': "SMART",
                'primaryExchange': contract.primaryExchange,
                'secIdType': contract.secIdType,
                'secId': contract.secId
            }

        return self.saved_contract_details[symbol]

    def symbolSamples(self, reqId: int,
                      contractDescriptions: ListOfContractDescription):
        """Callback from reqMatchingSymbols. Add contracts that are of
        secType=STK, currency=USD, and primaryExchange=(NYSE | ISLAND) to the
        class variable contract_search_results.
        """
        super().symbolSamples(reqId, contractDescriptions)

        # Add all contracts to the to a list that the calling function can
        # access.
        contracts = []
        for desc in contractDescriptions:
            contracts.append(desc.contract)
        # is complete.
        self._contract_details = contracts

    def make_contract(self, symbol):
        """Create a contract for the given symbol.

        Arguments:
        symbol (str): Ticker symbol
        """
        contract_info = self.get_contract_details(symbol)
        contract = Contract()
        contract.symbol = symbol
        contract.currency = contract_info['currency']
        contract.exchange = contract_info['exchange']
        contract.primaryExchange = contract_info['primaryExchange']
        contract.secType = contract_info['secType']
        contract.secId = contract_info['secId']

        return contract

    def get_positions(self):
        """Get the account positions. If the class variable, positions, exists,
        return that value, else call the EClient method reqPositions, wait for
        a short time and then return the class variable positions.

        Returns (dict): Dictionary of the positions information.
        """
        self.positions = []
        self.reqPositions()
        time.sleep(1)

        return pd.DataFrame.from_dict(self.positions).set_index('account')

    def position(self, account: str, contract: Contract, position: float,
                 avgCost: float):
        super().position(account, contract, position, avgCost)
        self.positions.append({
            'account': account,
            'symbol': contract.symbol,
            'secType': contract.secType,
            'position': position,
            'cost': avgCost
        })

    def positionEnd(self):
        """Cancel the position subscription after a return.
        """
        super().positionEnd()
        self.cancelPositions()

    def create_bracket_orders(self, req_orders=None):
        """Create orders, but do not place.

        Arguments:
        req_orders (list): list of dictionaries - keys are:
            symbol (str): Equity ticker symbol.
            instruction (str): "BUY" | "SELL"
            price (float): Order set price.
            quantity (float): Order quantity.
            outside_rth (bool): outside regular trading hours
            tif (str): Time in force "DAY" | "GTC"
            profit_price (float): Price for profit taking
            stop_price (float): Price for stop loss
            parent_id (int): Id of parent trade.
        """
        # If only a single contract (dict) is passed convert it
        # to a list with a single item.
        if not isinstance(req_orders, list):
            req_orders = [req_orders]

        for req_order in req_orders:
            contract = self.make_contract(symbol=req_order['symbol'])

            # Create the parent order
            order_id = self._get_next_order_id()
            parent = Order()
            parent.orderId = order_id
            parent.action = req_order['instruction']
            parent.orderType = "LMT"
            parent.totalQuantity = req_order['quantity']
            parent.lmtPrice = req_order['price']
            parent.outsideRth = req_order['outside_rth']
            parent.tif = req_order['tif']
            parent.transmit = False
            self._saved_orders[order_id] = {
                "order": parent, "contract": contract
            }

            # Create the profit taker order
            if req_order['profit_price'] is not None:
                order_id = self._get_next_order_id()
                profit_taker = Order()
                profit_taker.orderId = order_id
                profit_taker.action = "SELL"\
                    if req_order['instruction'] == "BUY" else "BUY"
                profit_taker.orderType = "LMT"
                profit_taker.totalQuantity = req_order['quantity']
                profit_taker.lmtPrice = req_order['profit_price']
                profit_taker.parentId = parent.orderId
                profit_taker.transmit = False
                self._saved_orders[order_id] = {
                    "order": profit_taker, "contract": contract
                }

            # Create stop loss order
            if req_order['stop_price'] is not None:
                order_id = self._get_next_order_id()
                stop_loss = Order()
                stop_loss.orderId = order_id
                stop_loss.action = "SELL"\
                    if req_order['instruction'] == "BUY" else "BUY"
                stop_loss.orderType = "STP"
                stop_loss.auxPrice = req_order['stop_price']
                stop_loss.totalQuantity = req_order['quantity']
                stop_loss.parentId = parent.orderId
                stop_loss.transmit = False
                self._saved_orders[order_id] = {
                    "order": stop_loss, "contract": contract
                }

    def create_trailing_stop_orders(self, req_orders=None):
        """Create a trailing stop order.

        Arguments:
        req_orders (list): list of dictionaries - keys are:
            symbol (str): Equity ticker symbol.
            instruction (str): "BUY" | "SELL"
            quantity (float): Order quantity.
            trail_stop_price (float): Trailing stop price
            trail_amount (float): Trailing amount in dollars.
            limit_offset (float): Offset of limit price
                for sell - limit offset is greater than trailing amount
                for buy - limit offset is less than trailing amount
            outside_rth (bool): outside regular trading hours
            tif (str): Time in force "DAY" | "GTC"
            parent_id (int): Id of parent trade.
        """
        # If only a single contract (dict) is passed convert it
        # to a list with a single item.
        if not isinstance(req_orders, list):
            req_orders = [req_orders]

        for req_order in req_orders:
            contract = self.make_contract(symbol=req_order['symbol'])

            # Create the order
            order_id = self._get_next_order_id()
            order = Order()
            order.orderId = order_id
            order.action = req_order['instruction']
            order.orderType = "TRAIL LIMIT"
            order.totalQuantity = req_order['quantity']
            order.trailStopPrice = req_order['trail_stop_price']
            order.auxPrice = req_order['trail_amount']
            order.lmtPriceOffset = req_order['limit_offset']
            order.outsideRth = req_order['outside_rth']
            order.tif = req_order['tif']
            order.transmit = False
            # TODO parent_id
            self._saved_orders[order_id] = {
                "order": order, "contract": contract
            }

    def create_stop_limit_orders(self, req_orders=None):
        """Create a trailing stop order.

        Arguments:
        req_orders (list): list of dictionaries - keys are:
            symbol (str): Equity ticker symbol.
            instruction (str): "BUY" | "SELL"
            quantity (float): Order quantity.
            stop_price (float): stop price
            limit_price (float): limit price.
            outside_rth (bool): outside regular trading hours
            tif (str): Time in force "DAY" | "GTC"
            profit_price (float): Profit taking price.
        """
        # If only a single contract (dict) is passed convert it
        # to a list with a single item.
        if not isinstance(req_orders, list):
            req_orders = [req_orders]

        for req_order in req_orders:
            contract = self.make_contract(symbol=req_order['symbol'])

            # Create the order
            order_id = self._get_next_order_id()
            order = Order()
            order.orderId = order_id
            order.action = req_order['instruction']
            order.orderType = "STP LMT"
            order.totalQuantity = req_order['quantity']
            order.lmtPrice = req_order['limit_price']
            order.auxPrice = req_order['stop_price']
            order.outsideRth = req_order['outside_rth']
            order.tif = req_order['tif']
            order.transmit = False
            self._saved_orders[order_id] = {
                "order": order, "contract": contract
            }

            # Create the profit taker order
            if req_order['profit_price'] is not None:
                profit_taker_order_id = self._get_next_order_id()
                profit_taker = Order()
                profit_taker.orderId = profit_taker_order_id
                profit_taker.action = "SELL"\
                    if req_order['instruction'] == "BUY" else "BUY"
                profit_taker.orderType = "LMT"
                profit_taker.totalQuantity = req_order['quantity']
                profit_taker.lmtPrice = req_order['profit_price']
                profit_taker.parentId = order.orderId
                profit_taker.transmit = False
                self._saved_orders[profit_taker_order_id] = {
                    "order": profit_taker, "contract": contract
                }

    def create_pegged_orders(self, req_orders=None):
        """Create a pegged to bench mark order.

        Arguments:
        req_orders (list): list of dictionaries - keys are:
            symbol (str): Equity ticker symbol.
            instruction (str): "BUY" | "SELL"
            quantity (float): Order quantity.
            starting_price (float): Order starting price.
            outside_rth (bool): outside regular trading hours
            tif (str): Time in force "DAY" | "GTC"
            peg_change_amount (float): Change of price for the target
            ref_change_amount (float): Change of price of the reference
            ref_contract_id (int): Contract ID of the reference
                SPY: ConID: 756733, exchange: ARCA
                QQQ: ConID: 320227571, exchange: NASDAQ
            ref_exchange (str): Exchange of the reference
            ref_price (float): Start price of the reference
            ref_lower_price (float): Lower ref price allowed
            ref_upper_price (float): Upper ref price allowed
        """
        # If only a single contract (dict) is passed convert it
        # to a list with a single item.
        if not isinstance(req_orders, list):
            req_orders = [req_orders]

        for req_order in req_orders:
            contract = self.make_contract(symbol=req_order['symbol'])

            # Create the parent order
            order_id = self._get_next_order_id()
            order = Order()
            order.orderId = order_id
            order.orderType = "PEG BENCH"
            order.action = req_order['instruction']
            order.totalQuantity = req_order['quantity']
            order.startingPrice = req_order['starting_price']
            order.isPeggedChangeAmountDecrease = False
            order.peggedChangeAmount = req_order['peg_change_amount']
            order.referenceChangeAmount = req_order['ref_change_amount']
            order.referenceContractId = req_order['ref_contract_id']
            order.referenceExchange = req_order['ref_exchange']
            order.stockRefPrice = req_order['ref_price']
            order.stockRangeLower = req_order['ref_lower_price']
            order.stockRangeUpper = req_order['ref_upper_price']
            order.transmit = False
            self._saved_orders[order_id] = {
                "order": order, "contract": contract
            }

    def get_saved_orders(self, symbol=None):
        """Return saved orders for symbol. If symbol is None
        return all saved orders.

        Returns (dict) {order_id: {order: order, contract: contract}}
        """
        if symbol is None:
            return self._saved_orders

        orders = dict()
        for oid, order in self._saved_orders.items():
            if order['contract'].symbol == symbol:
                orders[oid] = order
        return orders

    def place_order(self, order_id=None):
        """Place a saved order. from a previously created saved order with
        order_id.

        Arguments:
        order_id (int): The order_id of a previously created order.
        """
        if order_id in self._saved_orders:
            self.placeOrder(order_id, self._saved_orders[order_id]['contract'],
                            self._saved_orders[order_id]['order'])
        del self._saved_orders[order_id]

    def place_all_orders(self):
        """Place all the saved orders.
        """
        order_ids = list(self._saved_orders.keys())
        for order_id in order_ids:
            self.place_order(order_id=order_id)

    def get_open_orders(self):
        """Call the IBApi.EClient reqOpenOrders. Open orders are returned via
        the callback openOrder.
        """
        self.reqOpenOrders()

    def openOrder(self, orderId: OrderId, contract: Contract, order: Order,
                  orderState: OrderState):
        """Callback from reqOpenOrders(). Method is over-ridden from the
        EWrapper class.
        """
        super().openOrder(orderId, contract, order, orderState)
        self._open_orders.append({
            'order_id': orderId,
            'contract': contract,
            'order': order
        })

    def get_quotes(self, symbols=None):
        """Get a quote for the symbol. Callsback to
        Warning: This may incur fees!

        Arguments:
        symbols (str|list): Equity ticker symbol or list of ticker symbols.

        Returns (Panda Series): Last trade price for the symbols.
        """
        # If only a single symbol is passed convert it
        # to a list with a single item.
        if isinstance(symbols, str):
            symbols = [symbols]

        # Get the bar data for each symbol
        quotes = pd.Series(index=symbols)
        for symbol in symbols:
            quote = self._req_historical_data(
                symbol,
                end_date="",
                duration="2 D",
                size="1 min",
                info="TRADES",
                rth=False
            )
            quotes[symbol] = float(quote.iloc[-1]['close_price'])

        return quotes

    def get_price_history(self, symbols=None, start_date=None, end_date=None,
                          bar_size="1 day", rth=False):
        """Get the price history for symbols.

        Arguments:
        symbols (str|list): Equity ticker symbol or list of ticker symbols.
        start_date (datetime): First date for data retrieval.
        end_date (datetime): Last data for data retrieval.
        bar_size (str): Bar size (e.g. "1 min", "1 day", "1 month")
            for valid strings see:
               http://interactivebrokers.github.io/tws-api/historical_bars.html
        rth (bool): True to only return data within regular trading hours.

        return (pandas.DataFrame): Price history data.
        """
        if end_date is None:
            end_date = datetime.today()

        # If only a single symbol is passed convert it
        # to a list with a single item.
        if isinstance(symbols, str):
            symbols = [symbols]

        # Estimate a duration string for the given date span.
        # TODO fix duration of seconds
        duration = end_date - start_date
        if duration.days >= 365:
            duration = "{} Y".format(int(duration.days/365))
        elif duration.days < 365 and duration.days > 1:
            duration = "{} D".format(duration.days)
        else:
            duration = "{} S".format(duration.seconds)
        # Get the bar data for each symbol
        bars = {}
        for symbol in symbols:
            try:
                bars[symbol] = self._req_historical_data(
                    symbol,
                    end_date=end_date.strftime("%Y%m%d %H:%M:%S"),
                    duration=duration,
                    size=bar_size,
                    info="TRADES",
                    rth=rth
                )
            except HistoricalRequestError as err:
                print(err.message)

        # Format the bars dictionary for conversion into DataFrame
        bars = {(outerKey, innerKey): values for outerKey, innerDict
                in bars.items() for innerKey, values in innerDict.items()}
        bars = pd.DataFrame(bars)

        # Reindex the bars using real time stamps.
        if (bar_size.find("secs") != -1 or bar_size.find("min") != -1 or
            bar_size.find("hour") != -1):
            index = [datetime.strptime(d, "%Y-%m-%d %H:%M:%S")
                     for d in bars.index]
        else:
            index = [datetime.strptime(d, "%Y-%m-%d") for d in bars.index]
        bars.index = index

        # Try to get rid of any missing data.
        bars.fillna(method="ffill", inplace=True)

        return bars

    def _req_historical_data(self, symbol, end_date="", duration="20 D",
                             size="1 day", info="TRADES", rth=False):
        """Get historical data using reqHistoricalData. Upon completion the
        server will callback historicalData, which is overridden.
        http://interactivebrokers.github.io/tws-api/historical_bars.html#hd_duration

        Arguments:
        symbol (str): Ticker symbol
        end_date (datetime): Last date requested
        duration (str): How far to go back - valid options: (S, D, W, M, Y)
        size (str): Bar size (see link)
        info (str): Type of data to return (see link)
        rth (bool): Return data only in regular trading hours
        """
        contract = self.make_contract(symbol)

        self._historical_data = []
        self._historical_data_req_end = False
        self.reqHistoricalData(2001, contract, end_date, duration, size,
                               info, rth, 1, False, [])

        # Wait until the request has returned (make it blocking).
        start_time = datetime.now()
        while self._historical_data_req_end is not True:
            if (datetime.now() - start_time).microseconds > 1000000:
                raise HistoricalRequestError(
                    "Timeout occurred while retrieving price data for {}"
                    .format(symbol),
                    "_req_historical_data({})".format(symbol)
                )
            time.sleep(0.2)

        # Convert the data into
        bars_index = [b.date[:4]+"-"+b.date[4:6]+"-"+b.date[6:]
                      for b in self._historical_data]
        bars_data = [[float(b.open), float(b.high), float(b.low),
                      float(b.close), float(b.volume)]
                     for b in self._historical_data]

        bars = pd.DataFrame(
            index=bars_index,
            columns=['open_price', 'high', 'low', 'close_price', 'volume'],
            data=bars_data
        )

        return bars

    def historicalData(self, reqId: int, bar: BarData):
        """Overridden method from EWrapper. Checks to make sure reqId matches
        the self.historical_data[req_id] to confirm correct symbol.
        """
        self._historical_data.append(bar)

    def historicalDataEnd(self, reqId: int, start: str, end: str):
        """Overrides the EWrapper method.
        """
        self._historical_data_req_end = True

    def get_histogram(self, symbol=None, period="20 days"):
        """Get histograms of the symbols.

        Arguments:
        symbol (str): Equity ticker symbol or list of ticker symbols.
        period (str): Number of days to collect data.

        Returns (?): Histograms of the symbols
        """
        # If only a single symbol is passed convert it
        # to a list with a single item.

        contract = self.make_contract(symbol)
        self._histogram = None
        self.reqHistogramData(3001, contract, False, period)
        while self._histogram is None:
            time.sleep(0.2)

        histogram = pd.DataFrame(
            columns=["price", "count"],
            data=[[float(p.price), int(p.count)] for p in self._histogram]
        )

        return histogram

    def histogramData(self, reqId: int, items: HistogramDataList):
        """EWrapper method called from reqHistogramData.
        http://interactivebrokers.github.io/tws-api/histograms.html
        """
        self._histogram = items

    def keyboardInterrupt(self):
        """Stop exectution.
        """
        pass

    def quick_bracket(self, symbol=None, instruction=None, quantity=None,
                      amount=1000, limit_percent=None, profit_percent=None):
        """Calculate bracket order for symbol using a limit provided by
        limit_percent.

        Arguments
        symbol (str): Ticker symbol
        instruction (str): "BUY" | "SELL"
        quantity (int): Number of shares
        amount (float): Amount in dollars to trade
        limit_percent (float): Percent change from current quote to set limit.
        profit_percent (float): Percent change from limit price to take profit.

        Returns (dict) Parameters necessary to place a bracket order.
        """
        # Calculate a reasonable change if limit_percent is not given.
        if limit_percent is None:
            if instruction == "BUY":
                limit_percent = -0.3
            if instruction == "SELL":
                limit_percent = 0.3

        # Calculate a reasonable change if limit_percent is not given.
        if profit_percent is None:
            if instruction == "BUY":
                profit_percent = 0.3
            if instruction == "SELL":
                profit_percent = -0.3

        # Get the quote
        quote = self.get_quotes(symbol).loc[symbol]

        # Calculate the limit price from the limit_percent.
        limit_price = round(quote * (1 + limit_percent/100.), 2)
        # Calculate the profit price from the limit_price.
        profit_price = round(limit_price * (1 + profit_percent/100.), 2)

        # Calculate quantity if amount was provided.
        if quantity is None:
            quantity = int(amount / quote)

        req_order = {
            'symbol': symbol,
            'instruction': instruction,
            'quantity': quantity,
            'price': limit_price,
            'tif': "DAY",
            'outside_rth': True,
            'profit_price': profit_price,
            'stop_price': None
        }
        self.create_bracket_orders(req_orders=[req_order])

        for order_id in list(self.get_saved_orders(symbol).keys()):
            self.place_order(order_id=order_id)


def main(port=7497):
    """Entry point into the program.

    Arguments:
    port (int): Port number that IBGateway, or TWS is listening.
    """
    global API_THREAD
    try:
        app = IBApp()
        app.connect("127.0.0.1", port, clientId=0)
        print("serverVersion:%s connectionTime:%s" % (app.serverVersion(),
                                                      app.twsConnectionTime()))
        API_THREAD = threading.Thread(target=app.run)
        API_THREAD.start()
        return app
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    import sys
    # port number socker server is using (paper: 7497, live: 7496)
    PORT_NUMBER = sys.argv[1]
    main(port=PORT_NUMBER)
