Feature: Match win prediction
  As an API customer (a bettor or an analyst)
  I want win probabilities for a singles match-up, optionally on a given surface
  So that I can compare them with my own estimates or market prices

  Background:
    Given the following players exist:
      | id | name             | overall | hard | clay | grass | indoor |
      | 1  | Clay Specialist  | 1900    | 1850 | 2150 | 1600  | 1800   |
      | 2  | Grass Specialist | 1900    | 1850 | 1600 | 2150  | 1800   |
      | 3  | Journeyman       | 1500    | 1500 | 1500 | 1500  | 1500   |
    And I have a valid API key

  Rule: Probabilities are well-formed

    Scenario Outline: Win probabilities are between 0 and 1 and sum to 1
      When I request a prediction for player <a> against player <b> on <surface>
      Then the response status is 200
      And each win probability is between 0 and 1
      And the win probabilities sum to 1

      Examples:
        | a | b | surface     |
        | 1 | 2 | clay        |
        | 1 | 3 | grass       |
        | 3 | 2 | hard        |
        | 2 | 3 | indoor      |
        | 1 | 2 | any surface |

    Scenario: The order of the players does not change the answer
      When I request a prediction for player 1 against player 3 on clay
      And I request a prediction for player 3 against player 1 on clay
      Then both answers give each player the same win probability

  Rule: Predictions are surface-specific

    Scenario Outline: The favourite depends on the surface
      When I request a prediction for player 1 against player 2 on <surface>
      Then the response status is 200
      And "<favourite>" is the favourite
      And the response reports the surface as "<surface>"

      Examples:
        | surface | favourite        |
        | clay    | Clay Specialist  |
        | grass   | Grass Specialist |

    Scenario: Without a surface, overall ratings are used
      When I request a prediction for player 1 against player 2 on any surface
      Then the response status is 200
      And neither player is the favourite

    Scenario: Surface names are not case-sensitive
      When I request a prediction for player 1 against player 2 on Clay
      Then the response status is 200
      And "Clay Specialist" is the favourite
      And the response reports the surface as "clay"

  Rule: Invalid requests are rejected with a clear error

    Scenario Outline: An unknown surface is rejected
      When I request a prediction for player 1 against player 2 on <surface>
      Then the response status is 422
      And the error is "validation_error" on field "surface"

      Examples:
        | surface |
        | carpet  |
        | sand    |
        | ice     |

    Scenario: An unknown player is rejected
      When I request a prediction for player 1 against player 999 on clay
      Then the response status is 404
      And the error is "player_not_found"

    Scenario: A player cannot be predicted against themselves
      When I request a prediction for player 1 against player 1 on clay
      Then the response status is 422
      And the error is "validation_error"

    Scenario Outline: Malformed request bodies are rejected
      When I send a prediction request with the body:
        """
        <body>
        """
      Then the response status is 422
      And the error is "validation_error" on field "<field>"

      Examples:
        | body                                     | field       |
        | {"player_a_id": 1}                       | player_b_id |
        | {"player_a_id": "abc", "player_b_id": 2} | player_a_id |
        | {"player_a_id": 1, "player_b_id": 2.5}   | player_b_id |

  Rule: Only customers with a valid API key get predictions

    Scenario: An invalid API key is refused
      Given I use the API key "not-a-real-key"
      When I request a prediction for player 1 against player 2 on clay
      Then the response status is 401
      And the error is "invalid_api_key"
