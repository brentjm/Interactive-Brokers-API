# Interactive Brokers API

Python 3 classes to facilitate using the Interactive Brokers Python API. The class
creates a separate thread for the IB API, so that it can be used interactively from
a shell or Jupyter Lab.

## Getting Started

* Install Interactive Brokers Trader Work Station.
    * [TWS](https://www.interactivebrokers.com/en/index.php?f=14099#tws-software)
* Install Interactive Brokders API
    * [IB API](http://interactivebrokers.github.io/)
* Start the TWS (or IB Gateway software), and follow the instructions for settings on the API.
    * [API setup](http://interactivebrokers.github.io/tws-api/initial_setup.html)
* Clone this repository
    git clone https://github.com/brentjm/Interactive-Brokers-API.git
* Change the directory to the file containing ib_api.py
* In a python terminal (e.g. Jupyter Lab), get a reference to the API.
    $ib_api = ib_api.main(port=7496)
Use port 7497 for paper trading.

## Example
[Demo Jupyter Lab Notebook](http://htmlpreview.github.com/?https://github.com/brentjm/Interactive-Brokers-API/blob/master/InteractiveBrokersDemo.html)

# Author
**Brent Maranzano**

# License
This project is licensed under the MIT License - see the LICENSE.md file for details
